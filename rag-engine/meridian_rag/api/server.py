"""Retrieval HTTP API — the gateway's entry point into the RAG engine.

Endpoints:
    POST /embed     — embed texts (used by the gateway's semantic cache)
    POST /retrieve  — full retrieval path: embed query → ANN top-20 →
                      cross-encoder rerank top-5 → assembled context
    GET  /health    — liveness
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from meridian_rag.config import Settings, load_settings
from meridian_rag.retrieval.context_builder import build_context
from meridian_rag.retrieval.pgvector import ann_search
from meridian_rag.retrieval.reranker import Reranker
from meridian_rag.storage.migrations import verify_schema
from meridian_rag.storage.schema import session_factory

logger = logging.getLogger(__name__)

settings: Settings | None = None
sessions = None
reranker = Reranker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings, sessions
    settings = load_settings()
    sessions = session_factory(settings.database_url)
    await verify_schema(sessions)

    # Load the embedding model eagerly so the first request isn't slow.
    from meridian_rag.embedding.model_registry import get_embedder

    get_embedder(settings.embedding_model, settings.embedding_version, settings.storage_dim)
    logger.info("retrieval service ready")
    yield


app = FastAPI(title="meridian-rag", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=64)


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    model: str
    version: str


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1)
    tenant_id: str
    top_k: int = Field(default=20, ge=1, le=100)  # ANN candidates
    top_n: int = Field(default=5, ge=1, le=20)  # after rerank
    max_context_chars: int = Field(default=8000, ge=100)


class RetrievedChunkOut(BaseModel):
    id: str
    document_id: str
    content: str
    similarity: float
    rerank_score: float | None
    metadata: dict[str, Any]


class RetrieveResponse(BaseModel):
    context: str
    document_ids: list[str]
    chunks: list[RetrievedChunkOut]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "meridian-rag"}


@app.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest) -> EmbedResponse:
    import asyncio

    from meridian_rag.embedding.model_registry import get_embedder

    assert settings is not None
    embedder = get_embedder(
        settings.embedding_model, settings.embedding_version, settings.storage_dim
    )
    vectors = await asyncio.to_thread(embedder.embed, req.texts)
    return EmbedResponse(
        embeddings=vectors,
        model=settings.embedding_model,
        version=settings.embedding_version,
    )


@app.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    import asyncio

    from meridian_rag.embedding.model_registry import get_embedder

    assert settings is not None and sessions is not None
    embedder = get_embedder(
        settings.embedding_model, settings.embedding_version, settings.storage_dim
    )

    # 1. Embed the query with the same model used at index time (ADR-008).
    query_vec = (await asyncio.to_thread(embedder.embed, [req.query]))[0]

    # 2. ANN search — wide candidate set, tuned for recall.
    async with sessions() as session:
        candidates = await ann_search(
            session,
            tenant_id=req.tenant_id,
            query_embedding=query_vec,
            embedding_model=settings.embedding_model,
            top_k=req.top_k,
        )

    if not candidates:
        return RetrieveResponse(context="", document_ids=[], chunks=[])

    # 3. Cross-encoder rerank — narrow, tuned for precision.
    try:
        ranked = await reranker.rerank(req.query, candidates, top_n=req.top_n)
    except Exception as exc:  # noqa: BLE001 — degrade to ANN order, don't fail retrieval
        logger.warning("reranker failed, falling back to ANN order: %s", exc)
        ranked = candidates[: req.top_n]

    # 4. Assemble context; document_ids feed gateway cache invalidation tags.
    ctx = build_context(ranked, max_chars=req.max_context_chars)
    return RetrieveResponse(
        context=ctx.text,
        document_ids=ctx.document_ids,
        chunks=[
            RetrievedChunkOut(
                id=c.id,
                document_id=c.document_id,
                content=c.content,
                similarity=c.similarity,
                rerank_score=c.rerank_score,
                metadata=c.metadata,
            )
            for c in ranked
        ],
    )


def main() -> None:
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("RAG_PORT", "8090")))


if __name__ == "__main__":
    main()
