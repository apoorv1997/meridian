"""Dead-letter queue producer.

Failed messages go to meridian.dlq with the original payload, the error, and
a retry count so they can be investigated and replayed.
"""

import json
import logging
import time
from typing import Any

from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)

RETRY_COUNT_HEADER = "x-retry-count"


class DLQProducer:
    def __init__(self, brokers: str, topic: str, max_retries: int = 3) -> None:
        self._topic = topic
        self._max_retries = max_retries
        self._producer = AIOKafkaProducer(bootstrap_servers=brokers)

    async def start(self) -> None:
        await self._producer.start()

    async def stop(self) -> None:
        await self._producer.stop()

    async def send(
        self,
        original_value: bytes,
        error: Exception,
        retry_count: int,
        source_topic: str,
        source_partition: int,
        source_offset: int,
        key: bytes | None = None,
    ) -> None:
        """Publish a failed message to the DLQ with retry metadata."""
        envelope: dict[str, Any] = {
            "source_topic": source_topic,
            "source_partition": source_partition,
            "source_offset": source_offset,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "retry_count": retry_count,
            "failed_at": time.time(),
            "original_value": original_value.decode("utf-8", errors="replace"),
        }
        await self._producer.send_and_wait(
            self._topic,
            value=json.dumps(envelope).encode("utf-8"),
            key=key,
            headers=[(RETRY_COUNT_HEADER, str(retry_count).encode())],
        )
        logger.warning(
            "message sent to DLQ",
            extra={
                "source_topic": source_topic,
                "offset": source_offset,
                "error": str(error),
                "retry_count": retry_count,
            },
        )

    def should_retry(self, retry_count: int) -> bool:
        return retry_count < self._max_retries
