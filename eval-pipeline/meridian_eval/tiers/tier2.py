"""Tier 2 evaluation — sampled async LLM-as-judge (10% of spans by default).

Spans without input_text / output_text in their attributes are skipped —
there is no text for the judge to evaluate.  The sample rate and rubrics
are configurable per route; defaults apply when route config is absent.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from meridian_eval.judge.ensemble import EnsembleJudge, EnsembleResult

if TYPE_CHECKING:
    from meridian_eval.consumer.models import TraceSpan

logger = logging.getLogger(__name__)

DEFAULT_RUBRICS = ["faithfulness", "relevance", "groundedness"]
DEFAULT_SAMPLE_RATE = 0.10


@dataclass
class Tier2Result:
    sampled: bool
    skipped_reason: str = ""  # non-empty when sampled=False or no text available
    scores: dict[str, EnsembleResult] = field(default_factory=dict)
    aggregate_score: float = -1.0  # mean of valid rubric scores; -1 = no valid scores


def should_sample(rate: float = DEFAULT_SAMPLE_RATE) -> bool:
    return random.random() < rate  # noqa: S311 — not cryptographic


async def evaluate(
    span: TraceSpan,
    judge: EnsembleJudge,
    *,
    sample_rate: float = DEFAULT_SAMPLE_RATE,
    rubric_names: list[str] | None = None,
) -> Tier2Result:
    """Run Tier 2 evaluation for a single span.

    Returns immediately with sampled=False when the span is not selected or
    lacks the text attributes needed for LLM evaluation.
    """
    if not should_sample(sample_rate):
        return Tier2Result(sampled=False, skipped_reason="not_sampled")

    query = span.attributes.get("input_text", "").strip()
    response = span.attributes.get("output_text", "").strip()
    context = span.attributes.get("rag_context", "")

    if not query or not response:
        return Tier2Result(sampled=True, skipped_reason="no_text_attributes")

    names = rubric_names or DEFAULT_RUBRICS
    try:
        scores = await judge.score_all(names, query=query, response=response, context=context)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tier2 judge failed for trace %s: %s", span.trace_id, exc)
        return Tier2Result(sampled=True, skipped_reason=f"judge_error:{exc}")

    valid = [r.score for r in scores.values() if r.score >= 0.0]
    aggregate = sum(valid) / len(valid) if valid else -1.0

    return Tier2Result(sampled=True, scores=scores, aggregate_score=aggregate)
