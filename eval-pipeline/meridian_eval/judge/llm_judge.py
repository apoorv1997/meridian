"""LLM-as-judge using a local Ollama model (default: llama3.1:8b).

Sends a single rubric prompt to Ollama and parses the "SCORE: <float>"
line from the response.  Retries once on parse failure before giving up.
"""

from __future__ import annotations

import logging
import re

import httpx

from meridian_eval.judge import rubrics as _rubrics

logger = logging.getLogger(__name__)

_SCORE_RE = re.compile(r"SCORE:\s*([0-9]+(?:\.[0-9]+)?)")


class LLMJudge:
    """Thin async wrapper around the Ollama /api/generate endpoint."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def score(
        self,
        *,
        rubric_name: str,
        query: str,
        response: str,
        context: str = "",
    ) -> float:
        """Return a 0.0–1.0 quality score for the given rubric.

        Returns -1.0 if the judge call fails or the response cannot be parsed.
        """
        template = _rubrics.ALL_RUBRICS.get(rubric_name)
        if template is None:
            raise ValueError(f"unknown rubric: {rubric_name!r}")

        prompt = template.format(query=query, response=response, context=context)
        return await self._call(prompt)

    async def _call(self, prompt: str) -> float:
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 16},
        }
        try:
            resp = await self._client.post(f"{self._base_url}/api/generate", json=payload)
            resp.raise_for_status()
            text: str = resp.json().get("response", "")
            return _parse_score(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("judge call failed: %s", exc)
            return -1.0


def _parse_score(text: str) -> float:
    match = _SCORE_RE.search(text)
    if not match:
        logger.warning("judge response missing SCORE line: %r", text[:200])
        return -1.0
    score = float(match.group(1))
    return max(0.0, min(1.0, score))
