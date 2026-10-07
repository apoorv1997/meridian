"""Span attribute schema for Meridian OTel traces.

Must stay in sync with the Go models.TraceSpan struct in
gateway/pkg/models/models.go. The JSON keys here are the keys the eval
pipeline reads from Kafka.
"""

import uuid
from datetime import datetime, timezone


def build_span_dict(
    *,
    trace_id: str | None = None,
    span_id: str | None = None,
    tenant_id: str,
    route_id: str,
    provider: str,
    model: str,
    latency_ms: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    cached: bool = False,
    error: str = "",
    attributes: dict[str, str] | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> dict:
    """Return a dict matching the Go models.TraceSpan JSON schema."""
    now = datetime.now(timezone.utc)
    return {
        "trace_id": trace_id or str(uuid.uuid4()),
        "span_id": span_id or str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "route_id": route_id,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "cached": cached,
        "error": error,
        "attributes": attributes or {},
        "started_at": (started_at or now).isoformat(),
        "finished_at": (finished_at or now).isoformat(),
    }
