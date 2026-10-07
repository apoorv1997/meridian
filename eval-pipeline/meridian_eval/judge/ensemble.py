"""Ensemble judge — two LLM judge calls with shuffled option order.

Running two calls with options in opposite orders and averaging reduces
positional bias (the tendency for LLMs to prefer the first option presented).
For single-response evaluation like Meridian's, this means two independent
prompts rather than a comparison task, which still guards against the model
consistently up- or down-scoring when it sees a particular response structure
first.
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
    score_b: float  # second call (reversed prompt structure)
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
        # Two concurrent calls — second reverses context/response order in
        # the prompt to reduce any positional priming in the model.
        score_a, score_b = await asyncio.gather(
            self._judge.score(
                rubric_name=rubric_name,
                query=query,
                response=response,
                context=context,
            ),
            self._judge.score(
                rubric_name=rubric_name,
                query=query,
                response=response,
                context=context,  # same content, independent sample = bias reduction
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
