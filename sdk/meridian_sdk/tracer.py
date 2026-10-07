"""MeridianTracer — thin wrapper that routes LLM call spans to Kafka."""

import logging
import os

from meridian_sdk.exporters import KafkaSpanExporter

logger = logging.getLogger(__name__)


class MeridianTracer:
    """Routes LLM call spans to Kafka via KafkaSpanExporter.

    Usage::

        tracer = MeridianTracer(tenant_id="...", kafka_brokers="localhost:9094")
        await tracer.start()

        # ... wrap calls with @trace_llm_call or call tracer.emit() directly

        await tracer.stop()
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        kafka_brokers: str | None = None,
        traces_topic: str = "meridian.traces",
    ) -> None:
        brokers = kafka_brokers or os.environ.get("KAFKA_BROKERS", "localhost:9094")
        self._tenant_id = tenant_id
        self._exporter = KafkaSpanExporter(brokers, traces_topic)
        self._started = False

    async def start(self) -> None:
        await self._exporter.start()
        self._started = True
        logger.info("meridian tracer started", extra={"tenant_id": self._tenant_id})

    async def stop(self) -> None:
        await self._exporter.stop()
        self._started = False

    async def emit(self, span: dict) -> None:
        if not self._started:
            raise RuntimeError("call start() before emit()")
        await self._exporter.export(span)

    @property
    def tenant_id(self) -> str:
        return self._tenant_id
