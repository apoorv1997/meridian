"""@trace_llm_call — instrumentation decorator for LLM calls.

Usage::

    tracer = MeridianTracer(tenant_id="acme", kafka_brokers="localhost:9094")
    await tracer.start()

    @trace_llm_call(tracer=tracer, provider="anthropic", model="claude-sonnet-4-6")
    async def call_llm(prompt: str) -> str:
        # ... make the actual API call
        return response_text

The decorated function must return a string (the model response).  Token counts
are estimated from char length if not passed as keyword args ``input_tokens`` /
``output_tokens``.  Input text and output text are attached in ``attributes`` so
the eval pipeline can run LLM-as-judge on them.
"""

import asyncio
import functools
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from meridian_sdk.span_builder import build_span_dict
from meridian_sdk.tracer import MeridianTracer

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4  # rough estimate for cost guessing when counts unavailable


def trace_llm_call(
    *,
    tracer: MeridianTracer,
    provider: str,
    model: str,
    route_id: str = "default",
) -> Callable:
    """Decorator that emits a Meridian span after each LLM call completes."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            started_at = datetime.now(timezone.utc)
            t0 = time.monotonic()
            error_str = ""
            result = None
            try:
                result = await fn(*args, **kwargs)
                return result
            except Exception as exc:
                error_str = str(exc)
                raise
            finally:
                latency_ms = (time.monotonic() - t0) * 1000
                finished_at = datetime.now(timezone.utc)

                output_str = result if isinstance(result, str) else ""
                # Concatenate positional args as the "input" for token estimation.
                input_str = " ".join(str(a) for a in args)
                in_tokens = kwargs.pop(
                    "input_tokens", max(1, len(input_str) // _CHARS_PER_TOKEN)
                )
                out_tokens = kwargs.pop(
                    "output_tokens", max(0, len(output_str) // _CHARS_PER_TOKEN)
                )

                span = build_span_dict(
                    tenant_id=tracer.tenant_id,
                    route_id=route_id,
                    provider=provider,
                    model=model,
                    latency_ms=latency_ms,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    error=error_str,
                    attributes={
                        "input_text": input_str[:4096],
                        "output_text": output_str[:4096],
                    },
                    started_at=started_at,
                    finished_at=finished_at,
                )
                try:
                    await tracer.emit(span)
                except Exception as emit_exc:  # noqa: BLE001
                    logger.warning("span emit failed: %s", emit_exc)

        @functools.wraps(fn)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return asyncio.run(_async_wrapper(*args, **kwargs))

        return _async_wrapper if asyncio.iscoroutinefunction(fn) else _sync_wrapper

    return decorator
