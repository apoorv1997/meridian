"""Semantic drift detector: rolling-window centroid compared with a baseline.

Algorithm, per (tenant, route):
  1. Keep the last N output embeddings in a rolling window.
  2. When the window first fills, store its centroid (mean vector) as the baseline.
  3. After that, compare the current window's centroid with the baseline by cosine
     distance. Alert when it crosses the threshold.

Windows are kept per tenant and route because route IDs are only unique within a
tenant (route_configs is UNIQUE(tenant_id, route_id)); keying on the route alone would
mix two tenants' outputs into one baseline.

The centroid comes from a running sum, so each new embedding costs O(dimensions)
rather than re-adding the whole window. The sum is recomputed exactly once per window
of additions so floating-point error can't accumulate.

An alert fires when a route moves into drift, not on every output while it stays
there; it re-arms once the route comes back under its threshold.
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
    tenant_id: str
    route_id: str
    distance: float
    threshold: float
    window_size: int


@dataclass
class _RouteWindow:
    vectors: deque[list[float]]
    total: list[float]
    adds_since_recompute: int = 0
    baseline: list[float] | None = None
    drifting: bool = False
    dim: int = 0


class DriftDetector:
    """Rolling-window drift detector, one window per (tenant, route)."""

    def __init__(
        self,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        default_threshold: float = _DEFAULT_THRESHOLD,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size must be at least 1")
        self._window_size = window_size
        self._default_threshold = default_threshold
        self._windows: dict[tuple[str, str], _RouteWindow] = {}
        self._thresholds: dict[tuple[str, str], float] = {}

    def set_threshold(self, tenant_id: str, route_id: str, threshold: float) -> None:
        """Override the drift threshold for one route (route_configs.drift_threshold)."""
        self._thresholds[(tenant_id, route_id)] = threshold

    def threshold(self, tenant_id: str, route_id: str) -> float:
        return self._thresholds.get((tenant_id, route_id), self._default_threshold)

    def add(self, tenant_id: str, route_id: str, embedding: list[float]) -> DriftAlert | None:
        """Add one output embedding; return a DriftAlert if the route just moved into drift."""
        key = (tenant_id, route_id)
        win = self._windows.get(key)
        if win is None or win.dim != len(embedding):
            if win is not None:
                # A different embedding size means a different model: the old baseline
                # is in another vector space, so start over.
                logger.warning(
                    "drift window reset: embedding size changed",
                    extra={"tenant_id": tenant_id, "route_id": route_id,
                           "old_dim": win.dim, "new_dim": len(embedding)},
                )
            win = _RouteWindow(
                vectors=deque(maxlen=self._window_size),
                total=[0.0] * len(embedding),
                dim=len(embedding),
            )
            self._windows[key] = win

        if len(win.vectors) == self._window_size:
            evicted = win.vectors[0]
            for i, x in enumerate(evicted):
                win.total[i] -= x
        win.vectors.append(embedding)
        for i, x in enumerate(embedding):
            win.total[i] += x
        win.adds_since_recompute += 1
        if win.adds_since_recompute >= self._window_size:
            win.total = _sum(win.vectors, win.dim)
            win.adds_since_recompute = 0

        if len(win.vectors) < self._window_size:
            return None  # window not full yet: nothing to compare

        n = len(win.vectors)
        centroid = [x / n for x in win.total]
        if win.baseline is None:
            win.baseline = centroid
            logger.info("drift baseline set", extra={"tenant_id": tenant_id, "route_id": route_id})
            return None

        distance = _cosine_distance(centroid, win.baseline)
        threshold = self.threshold(tenant_id, route_id)
        if distance <= threshold:
            win.drifting = False
            return None
        if win.drifting:
            return None  # already alerted for this episode
        win.drifting = True
        logger.warning(
            "semantic drift detected",
            extra={"tenant_id": tenant_id, "route_id": route_id,
                   "distance": round(distance, 4), "threshold": threshold},
        )
        return DriftAlert(tenant_id, route_id, distance, threshold, n)

    def reset_baseline(self, tenant_id: str, route_id: str) -> None:
        """Forget a route's baseline, e.g. after a deliberate model change.

        The window is cleared too, so the new baseline is built only from outputs that
        arrive after the reset, not from a mix of old and new ones.
        """
        win = self._windows.get((tenant_id, route_id))
        if win is not None:
            win.vectors.clear()
            win.total = [0.0] * win.dim
            win.adds_since_recompute = 0
            win.baseline = None
            win.drifting = False
        logger.info("drift baseline reset", extra={"tenant_id": tenant_id, "route_id": route_id})


def _sum(vectors: deque[list[float]], dim: int) -> list[float]:
    total = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            total[i] += x
    return total


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0  # a zero vector has no direction: treat it as maximally distant
    return 1.0 - dot / (norm_a * norm_b)
