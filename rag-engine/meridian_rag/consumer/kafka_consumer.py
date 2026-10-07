"""Document ingestion consumer.

Pipeline (per ingestion contract):
    Kafka meridian.documents
        → parse event
        → content hash dedup (duplicate → commit + skip)
        → chunk (fixed or semantic)
        → bounded embedding worker pool (backpressure on pgvector write lag)
        → pgvector insert with embedding_model + embedding_version
        → cache invalidation for the affected document
        → manual offset commit (never auto-commit)
    Failures → meridian.dlq with retry metadata.
"""

import asyncio
import logging

from aiokafka import AIOKafkaConsumer
from pydantic import ValidationError
from sqlalchemy import delete, text

from meridian_rag.chunking import fixed_size_chunks, semantic_chunks
from meridian_rag.config import Settings, load_settings
from meridian_rag.consumer.dlq import DLQProducer
from meridian_rag.consumer.idempotency import content_hash, is_duplicate
from meridian_rag.embedding.worker import EmbeddingWorkerPool
from meridian_rag.models import Chunk, DocumentEvent, DocumentEventType
from meridian_rag.storage.schema import ChunkRow, session_factory

logger = logging.getLogger(__name__)


class DocumentConsumer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sessions = session_factory(settings.database_url)
        # aiokafka clients must be constructed inside a running event loop —
        # created in run(), not here.
        self._consumer: AIOKafkaConsumer | None = None
        self._dlq: DLQProducer | None = None
        self._pool: EmbeddingWorkerPool | None = None

    async def run(self) -> None:
        self._consumer = AIOKafkaConsumer(
            self.settings.documents_topic,
            bootstrap_servers=self.settings.kafka_brokers,
            group_id=self.settings.consumer_group,
            enable_auto_commit=False,  # manual commit only — never auto-commit
            auto_offset_reset="earliest",
        )
        self._dlq = DLQProducer(
            self.settings.kafka_brokers, self.settings.dlq_topic, self.settings.max_dlq_retries
        )
        self._pool = EmbeddingWorkerPool(self.settings, self._sessions)

        await self._consumer.start()
        await self._dlq.start()
        await self._pool.start()
        logger.info(
            "document consumer started",
            extra={"topic": self.settings.documents_topic, "group": self.settings.consumer_group},
        )
        try:
            async for message in self._consumer:
                try:
                    await self._process(message.value)
                except Exception as exc:  # noqa: BLE001 — every failure goes to DLQ
                    await self._dlq.send(
                        original_value=message.value,
                        error=exc,
                        retry_count=0,
                        source_topic=message.topic,
                        source_partition=message.partition,
                        source_offset=message.offset,
                        key=message.key,
                    )
                # Commit after successful processing OR after DLQ handoff —
                # the message is accounted for either way.
                await self._consumer.commit()
        finally:
            await self._pool.stop()
            await self._dlq.stop()
            await self._consumer.stop()

    async def _process(self, raw: bytes) -> None:
        try:
            event = DocumentEvent.model_validate_json(raw)
        except ValidationError as exc:
            raise ValueError(f"invalid document event: {exc}") from exc

        if event.type == DocumentEventType.DELETE:
            await self._delete_document(event)
            return

        doc_hash = content_hash(event.content)
        async with self._sessions() as session:
            if await is_duplicate(session, event.tenant_id, event.document_id, doc_hash):
                logger.info(
                    "duplicate content, skipping",
                    extra={"document_id": event.document_id, "hash": doc_hash[:12]},
                )
                return

        chunks = self._chunk(event.content)
        if not chunks:
            logger.info("empty document, nothing to index", extra={"document_id": event.document_id})
            return

        chunk_objs = [
            Chunk(
                tenant_id=event.tenant_id,
                document_id=event.document_id,
                content=chunk_text,
                content_hash=doc_hash,
                chunk_index=i,
                embedding_model=self.settings.embedding_model,
                embedding_version=self.settings.embedding_version,
                metadata=event.metadata,
            )
            for i, chunk_text in enumerate(chunks)
        ]

        # On update: replace prior chunks for this document atomically with
        # the new version's chunks (handled inside the worker write).
        await self._pool.submit(chunk_objs, replace_document=True)

        await self._invalidate_cache(event.tenant_id, event.document_id)

    def _chunk(self, content: str) -> list[str]:
        if self.settings.chunk_strategy == "fixed":
            return fixed_size_chunks(
                content, self.settings.chunk_size, self.settings.chunk_overlap
            )
        return semantic_chunks(content, self.settings.chunk_size)

    async def _delete_document(self, event: DocumentEvent) -> None:
        async with self._sessions() as session:
            await session.execute(
                delete(ChunkRow).where(
                    ChunkRow.tenant_id == event.tenant_id,
                    ChunkRow.document_id == event.document_id,
                )
            )
            await session.commit()
        await self._invalidate_cache(event.tenant_id, event.document_id)
        logger.info("document deleted", extra={"document_id": event.document_id})

    async def _invalidate_cache(self, tenant_id: str, document_id: str) -> None:
        """Evict gateway semantic-cache entries that cite this document."""
        async with self._sessions() as session:
            await session.execute(
                text(
                    "DELETE FROM cache_entries "
                    "WHERE tenant_id = :tenant_id AND :document_id = ANY(document_ids)"
                ),
                {"tenant_id": tenant_id, "document_id": document_id},
            )
            await session.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    consumer = DocumentConsumer(load_settings())
    asyncio.run(consumer.run())


if __name__ == "__main__":
    main()
