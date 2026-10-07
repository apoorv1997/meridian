"""Kafka span exporter — writes spans to the meridian.traces topic."""

import json
import logging
from typing import Any

from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)


class KafkaSpanExporter:
    """Async Kafka producer that serialises spans to JSON and writes to meridian.traces."""

    def __init__(self, bootstrap_servers: str, topic: str = "meridian.traces") -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode(),
        )
        await self._producer.start()
        logger.info("kafka span exporter started", extra={"topic": self._topic})

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()

    async def export(self, span: dict[str, Any]) -> None:
        if self._producer is None:
            raise RuntimeError("exporter not started — call start() first")
        await self._producer.send_and_wait(self._topic, span)
        logger.debug("span exported", extra={"trace_id": span.get("trace_id")})
