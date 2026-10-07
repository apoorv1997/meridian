"""Pydantic models for data consumed from Kafka.

TraceSpan mirrors the Go models.TraceSpan struct (gateway/pkg/models/models.go).
Field names and JSON keys must stay in sync with that struct.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TraceSpan(BaseModel):
    trace_id: str
    span_id: str = ""
    tenant_id: str
    route_id: str
    provider: str
    model: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False
    error: str = ""
    # Populated by SDK-instrumented callers; may be empty for gateway-only spans.
    # Keys of interest: "input_text", "output_text", "rag_context"
    attributes: dict[str, str] = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime
