"""Bounded async embedding worker pool.

submit() blocks when the queue is full — that blocking propagates upstream to
the Kafka consumer, which stops pulling new messages. Queue growth is bounded
by design; backpressure on pgvector writes slows the workers, which fills the
queue, which pauses consumption.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from meridian_rag.config import Settings
from meridian_rag.embedding.backpressure import BackpressureController
from meridian_rag.models import Chunk
from meridian_rag.storage.schema import ChunkRow

logger = logging.getLogger(__name__)


class _Job:
    __slots__ = ("chunks", "replace_document", "done")

    def __init__(self, chunks: list[Chunk], replace_document: bool) -> None:
        self.chunks = chunks
        self.replace_document = replace_document
        self.done: asyncio.Future[None] = asyncio.get_event_loop().create_future()


class EmbeddingWorkerPool:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
    ) -> None:
        self.settings = settings
        self._sessions = sessions
        self._queue: asyncio.Queue[_Job] = asyncio.Queue(maxsize=settings.queue_max_size)
        self._workers: list[asyncio.Task[None]] = []
        self.backpressure = BackpressureController(
            p99_threshold_ms=settings.write_p99_threshold_ms
        )
        # Injectable for tests; defaults to the real sentence-transformers model.
        self._embed_fn = embed_fn

    async def start(self) -> None:
        if self._embed_fn is None:
            from meridian_rag.embedding.model_registry import get_embedder

            embedder = get_embedder(
                self.settings.embedding_model,
                self.settings.embedding_version,
                self.settings.storage_dim,
            )
            self._embed_fn = embedder.embed

        for i in range(self.settings.worker_count):
            self._workers.append(asyncio.create_task(self._worker(i)))
        logger.info("embedding worker pool started", extra={"workers": len(self._workers)})

    async def stop(self) -> None:
        await self._queue.join()
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def submit(self, chunks: list[Chunk], replace_document: bool = False) -> None:
        """Enqueue chunks and wait for them to be embedded and written.

        Blocks while the queue is full (bounded queue = consumer backpressure)
        and raises if the worker failed, so the caller can DLQ the message.
        """
        job = _Job(chunks, replace_document)
        await self._queue.put(job)
        await job.done

    async def _worker(self, worker_id: int) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._process_job(job)
                job.done.set_result(None)
            except Exception as exc:  # noqa: BLE001 — propagated to submitter
                if not job.done.done():
                    job.done.set_exception(exc)
            finally:
                self._queue.task_done()

    async def _process_job(self, job: _Job) -> None:
        assert self._embed_fn is not None
        texts = [c.content for c in job.chunks]
        # CPU-bound model inference off the event loop.
        embeddings = await asyncio.to_thread(self._embed_fn, texts)
        for chunk, vector in zip(job.chunks, embeddings, strict=True):
            chunk.embedding = vector

        await self.backpressure.wait_if_needed()

        start = time.monotonic()
        await self._write(job)
        self.backpressure.record_write_latency((time.monotonic() - start) * 1000)

    async def _write(self, job: _Job) -> None:
        first = job.chunks[0]
        async with self._sessions() as session:
            if job.replace_document:
                # Replace prior version's chunks in the same transaction.
                await session.execute(
                    delete(ChunkRow).where(
                        ChunkRow.tenant_id == first.tenant_id,
                        ChunkRow.document_id == first.document_id,
                    )
                )
            session.add_all(
                ChunkRow(
                    id=c.id,
                    tenant_id=c.tenant_id,
                    document_id=c.document_id,
                    content=c.content,
                    content_hash=c.content_hash,
                    embedding=c.embedding,
                    embedding_model=c.embedding_model,
                    embedding_version=c.embedding_version,
                    chunk_index=c.chunk_index,
                    meta=c.metadata,
                )
                for c in job.chunks
            )
            await session.commit()
        logger.info(
            "chunks written",
            extra={"document_id": first.document_id, "count": len(job.chunks)},
        )
