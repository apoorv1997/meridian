from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from meridian_eval.consumer.models import TraceSpan


@pytest.fixture
def make_span() -> Callable[..., TraceSpan]:
    """Build a TraceSpan with sensible defaults; override any field by keyword."""

    def _make(**overrides: object) -> TraceSpan:
        now = datetime.now(UTC)
        fields: dict[str, object] = {
            "trace_id": "t1",
            "tenant_id": "tenant-a",
            "route_id": "r1",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "latency_ms": 800.0,
            "output_tokens": 120,
            "started_at": now,
            "finished_at": now,
        }
        fields.update(overrides)
        return TraceSpan(**fields)

    return _make
