"""TraceSpan must accept exactly what the gateway's Go models.TraceSpan marshals."""

import json

from meridian_eval.consumer.models import TraceSpan


def test_parses_a_span_as_the_gateway_emits_it() -> None:
    # json.Marshal of the Go struct: RFC 3339 timestamps with nanoseconds and offsets,
    # and "error" / "attributes" dropped by omitempty.
    payload = {
        "trace_id": "t1",
        "span_id": "s1",
        "tenant_id": "tenant-a",
        "route_id": "r1",
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "latency_ms": 812.5,
        "input_tokens": 10,
        "output_tokens": 20,
        "cost_usd": 0.001,
        "cached": False,
        "started_at": "2026-10-07T12:00:00.123456789Z",
        "finished_at": "2026-10-07T05:00:00.936956789-07:00",
    }
    span = TraceSpan.model_validate_json(json.dumps(payload))
    assert span.error == "" and span.attributes == {}
    assert span.started_at.microsecond == 123456  # nanoseconds truncated, not rejected
    assert (span.finished_at - span.started_at).total_seconds() > 0
