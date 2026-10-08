"""5-minute rolling window feedback aggregation.

Accumulates Tier 2 scores per (tenant, route, provider) and computes weight
deltas once the window closes and the minimum sample count is met.

Rules (ADR-005):
  - Window duration: 5 minutes
  - Minimum samples before any weight change: 50
  - Maximum weight shift per window: 10% (enforced by WeightUpdater)
  - Score → delta mapping: linear, centred at 0.5
      delta = (mean_score - 0.5) * max_shift * 2
      e.g. score=0.7 → delta=+0.04 (with max_shift=0.10)
           score=0.3 → delta=-0.04
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from meridian_eval.feedback.weight_updater import WeightUpdater

logger = logging.getLogger(__name__)


@dataclass
class _Bucket:
    scores: list[float] = field(default_factory=list)
    window_start: float = field(default_factory=time.monotonic)


class FeedbackWindow:
    """Aggregates Tier 2 scores and applies weight deltas each window period."""

    def __init__(
        self,
        updater: WeightUpdater,
        *,
        window_seconds: float = 300.0,   # 5 minutes
        min_samples: int = 50,
        max_shift: float = 0.10,
    ) -> None:
        self._updater = updater
        self._window_seconds = window_seconds
        self._min_samples = min_samples
        self._max_shift = max_shift
        # (tenant_id, route_id) → provider → _Bucket. Route IDs are only unique within a
        # tenant, so scores from two tenants' same-named routes must stay apart.
        self._buckets: dict[tuple[str, str], dict[str, _Bucket]] = defaultdict(
            lambda: defaultdict(_Bucket)
        )
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    def start(self) -> None:
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

    async def record(self, tenant_id: str, route_id: str, provider: str, score: float) -> None:
        """Record a Tier 2 score for one provider on one tenant's route."""
        if score < 0.0:
            return  # invalid score from failed judge call — discard
        async with self._lock:
            self._buckets[(tenant_id, route_id)][provider].scores.append(score)

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._window_seconds)
            await self._flush()

    async def _flush(self) -> None:
        async with self._lock:
            snapshot = {key: dict(providers) for key, providers in self._buckets.items()}
            self._buckets.clear()

        for (tenant_id, route_id), providers in snapshot.items():
            deltas: dict[str, float] = {}
            for provider, bucket in providers.items():
                n = len(bucket.scores)
                if n < self._min_samples:
                    logger.debug(
                        "skipping weight update: insufficient samples",
                        extra={"tenant_id": tenant_id, "route_id": route_id,
                               "provider": provider, "n": n},
                    )
                    continue
                mean_score = sum(bucket.scores) / n
                # Linear mapping: 0.5 → 0 delta, 1.0 → +max_shift, 0.0 → -max_shift
                delta = (mean_score - 0.5) * self._max_shift * 2
                deltas[provider] = delta
                logger.info(
                    "window closed",
                    extra={
                        "tenant_id": tenant_id,
                        "route_id": route_id,
                        "provider": provider,
                        "n": n,
                        "mean_score": round(mean_score, 4),
                        "delta": round(delta, 4),
                    },
                )

            if deltas:
                try:
                    await self._updater.apply(tenant_id, route_id, deltas)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "weight update failed",
                        extra={"tenant_id": tenant_id, "route_id": route_id, "error": str(exc)},
                    )
