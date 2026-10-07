"""Ensemble judge — two LLM judge calls with the evidence in opposite orders.

LLM judges are sensitive to where things sit in the prompt. The first call presents
the rubric's evidence sections in their normal order (for example context, question,
response); the second presents them reversed. Averaging the two cancels a judge's
preference for whatever it reads first or last.

The two prompts must differ: the judge runs at temperature 0, so sending the same
prompt twice would return the same score twice and add cost without reducing bias.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from meridian_eval.judge.llm_judge import LLMJudge

logger = logging.getLogger(__name__)


@dataclass
class EnsembleResult:
    rubric: str
    score_a: float  # first call
    score_b: float  # second call, evidence sections reversed
    score: float    # averaged; -1.0 if both calls failed


class EnsembleJudge:
    """Runs two judge calls per rubric and averages valid scores."""

    def __init__(self, judge: LLMJudge) -> None:
        self._judge = judge

    async def score(
        self,
        *,
        rubric_name: str,
        query: str,
        response: str,
        context: str = "",
    ) -> EnsembleResult:
        score_a, score_b = await asyncio.gather(
            self._judge.score(
                rubric_name=rubric_name, query=query, response=response, context=context
            ),
            self._judge.score(
                rubric_name=rubric_name,
                query=query,
                response=response,
                context=context,
                reverse_evidence=True,
            ),
        )

        valid = [s for s in (score_a, score_b) if s >= 0.0]
        averaged = sum(valid) / len(valid) if valid else -1.0

        return EnsembleResult(
            rubric=rubric_name,
            score_a=score_a,
            score_b=score_b,
            score=averaged,
        )

    async def score_all(
        self,
        rubric_names: list[str],
        *,
        query: str,
        response: str,
        context: str = "",
    ) -> dict[str, EnsembleResult]:
        """Score multiple rubrics concurrently."""
        results = await asyncio.gather(
            *(
                self.score(
                    rubric_name=name,
                    query=query,
                    response=response,
                    context=context,
                )
                for name in rubric_names
            )
        )
        return {r.rubric: r for r in results}
