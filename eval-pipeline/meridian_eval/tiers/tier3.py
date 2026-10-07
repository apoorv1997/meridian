"""Tier 3 evaluation — on-demand full ensemble (placeholder).

Tier 3 is triggered manually or by an operator API call for spans that need
deeper investigation (e.g., user-reported bad responses, anomaly alerts).
It re-runs the full EnsembleJudge across all rubrics with no sampling gate.

Not wired into the consumer pipeline automatically — invoked directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from meridian_eval.judge.ensemble import EnsembleJudge, EnsembleResult
from meridian_eval.judge.rubrics import ALL_RUBRICS

if TYPE_CHECKING:
    from meridian_eval.consumer.models import TraceSpan

logger = logging.getLogger(__name__)


@dataclass
class Tier3Result:
    trace_id: str
    scores: dict[str, EnsembleResult] = field(default_factory=dict)
    aggregate_score: float = -1.0


async def evaluate(
    span: TraceSpan,
    judge: EnsembleJudge,
) -> Tier3Result:
    """Run full ensemble across all rubrics for a single span."""
    query = span.attributes.get("input_text", "").strip()
    response = span.attributes.get("output_text", "").strip()
    context = span.attributes.get("rag_context", "")

    if not query or not response:
        logger.warning("tier3: span %s has no text attributes, skipping", span.trace_id)
        return Tier3Result(trace_id=span.trace_id)

    scores = await judge.score_all(
        list(ALL_RUBRICS.keys()),
        query=query,
        response=response,
        context=context,
    )

    valid = [r.score for r in scores.values() if r.score >= 0.0]
    aggregate = sum(valid) / len(valid) if valid else -1.0

    return Tier3Result(trace_id=span.trace_id, scores=scores, aggregate_score=aggregate)
