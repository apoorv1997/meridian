"""Backpressure on pgvector write lag.

Workers record every write latency into a rolling window. When the window's
p99 exceeds the threshold, workers apply exponential backoff before the next
write, doubling the delay while pressure persists and resetting once writes
recover. The bounded queue upstream (in the worker pool) ensures memory never
grows without bound while workers are backing off.
"""

import asyncio
import logging
import math
from collections import deque

logger = logging.getLogger(__name__)


class BackpressureController:
    def __init__(
        self,
        p99_threshold_ms: float = 200.0,
        window_size: int = 100,
        base_delay_s: float = 0.1,
        max_delay_s: float = 10.0,
    ) -> None:
        self._threshold_ms = p99_threshold_ms
        self._latencies: deque[float] = deque(maxlen=window_size)
        self._base_delay = base_delay_s
        self._max_delay = max_delay_s
        self._consecutive_pressure = 0
        self._lock = asyncio.Lock()

    def record_write_latency(self, latency_ms: float) -> None:
        self._latencies.append(latency_ms)

    def p99(self) -> float:
        if not self._latencies:
            return 0.0
        ordered = sorted(self._latencies)
        index = math.ceil(0.99 * len(ordered)) - 1
        return ordered[max(index, 0)]

    def under_pressure(self) -> bool:
        # Need a minimal sample before declaring pressure.
        return len(self._latencies) >= 10 and self.p99() > self._threshold_ms

    def current_delay(self) -> float:
        """Exponential backoff: base * 2^(n-1), capped at max_delay."""
        if self._consecutive_pressure == 0:
            return 0.0
        return min(self._base_delay * (2 ** (self._consecutive_pressure - 1)), self._max_delay)

    async def wait_if_needed(self) -> float:
        """Call before each write. Returns the delay applied (for observability)."""
        async with self._lock:
            if self.under_pressure():
                self._consecutive_pressure += 1
            else:
                if self._consecutive_pressure > 0:
                    logger.info("backpressure released")
                self._consecutive_pressure = 0
            delay = self.current_delay()

        if delay > 0:
            logger.warning(
                "backpressure active",
                extra={"p99_ms": round(self.p99(), 1), "delay_s": round(delay, 2)},
            )
            await asyncio.sleep(delay)
        return delay
