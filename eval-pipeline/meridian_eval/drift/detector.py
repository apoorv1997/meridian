"""Semantic drift detector — rolling window centroid comparison.

Algorithm:
  1. Maintain a rolling buffer of the last N output embeddings per route.
  2. Compute the centroid (mean vector) for each window.
  3. On the first window, store it as the baseline centroid.
  4. For subsequent windows, compute cosine distance between the current
     centroid and the baseline.  If distance > threshold, fire an alert.

The threshold is configurable per route (stored in route_configs.drift_threshold).
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_SIZE = 1000
_DEFAULT_THRESHOLD = 0.15


@dataclass
class DriftAlert:
    route_id: str
    distance: float
    threshold: float
    window_size: int


class DriftDetector:
    """Per-route rolling window drift detector."""

    def __init__(
        self,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        default_threshold: float = _DEFAULT_THRESHOLD,
    ) -> None:
        self._window_size = window_size
        self._default_threshold = default_threshold
        # route_id → deque of embedding vectors
        self._buffers: dict[str, deque[list[float]]] = {}
        # route_id → baseline centroid (set after first window fills)
        self._baselines: dict[str, list[float]] = {}

    def add(self, route_id: str, embedding: list[float]) -> DriftAlert | None:
        """Add one output embedding and return a DriftAlert if drift is detected."""
        if route_id not in self._buffers:
            self._buffers[route_id] = deque(maxlen=self._window_size)

        buf = self._buffers[route_id]
        buf.append(embedding)

        if len(buf) < self._window_size:
            return None  # buffer not yet full — no comparison possible

        centroid = _centroid(list(buf))

        if route_id not in self._baselines:
            self._baselines[route_id] = centroid
            logger.info("drift baseline set", extra={"route_id": route_id})
            return None

        distance = _cosine_distance(centroid, self._baselines[route_id])
        threshold = self._default_threshold

        if distance > threshold:
            logger.warning(
                "semantic drift detected",
                extra={
                    "route_id": route_id,
                    "distance": round(distance, 4),
                    "threshold": threshold,
                },
            )
            return DriftAlert(
                route_id=route_id,
                distance=distance,
                threshold=threshold,
                window_size=len(buf),
            )

        return None

    def reset_baseline(self, route_id: str) -> None:
        """Reset the baseline for a route (e.g. after a deliberate model change)."""
        self._baselines.pop(route_id, None)
        logger.info("drift baseline reset", extra={"route_id": route_id})


def _centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    total = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            total[i] += x
    n = len(vectors)
    return [x / n for x in total]


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0  # treat zero vector as maximally distant
    return 1.0 - dot / (norm_a * norm_b)
