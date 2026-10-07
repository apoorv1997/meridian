"""LLM-as-judge using a local Ollama model (default: llama3.1:8b).

Sends a single rubric prompt to Ollama and parses the "SCORE: <value>" line
from the response. A fraction such as "7/10" is read as 0.7. Anything outside
0.0–1.0, such as a bare "8" on a 0–10 scale, is rejected rather than clamped,
so a judge answering on the wrong scale can't feed inflated scores to routing.
"""

from __future__ import annotations

import logging
import re

import httpx

from meridian_eval.judge import rubrics as _rubrics

logger = logging.getLogger(__name__)

_SCORE_RE = re.compile(r"SCORE:\s*(\d*\.?\d+)(?:\s*/\s*(\d*\.?\d+))?", re.IGNORECASE)


class LLMJudge:
    """Thin async wrapper around the Ollama /api/generate endpoint."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.1:8b",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def score(
        self,
        *,
        rubric_name: str,
        query: str,
        response: str,
        context: str = "",
        reverse_evidence: bool = False,
    ) -> float:
        """Return a 0.0–1.0 quality score for the given rubric.

        ``reverse_evidence`` presents the context, question and response sections in
        reverse order. Returns -1.0 if the judge call fails or the reply can't be parsed.
        """
        rubrics = _rubrics.REVERSED_RUBRICS if reverse_evidence else _rubrics.ALL_RUBRICS
        template = rubrics.get(rubric_name)
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
    if match.group(2) is not None:
        denominator = float(match.group(2))
        if denominator == 0.0:
            logger.warning("judge score has a zero denominator: %r", text[:200])
            return -1.0
        score /= denominator
    if not 0.0 <= score <= 1.0:
        logger.warning("judge score outside 0.0-1.0: %r", text[:200])
        return -1.0
    return score
