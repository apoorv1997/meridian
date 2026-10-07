"""pgvector ANN search over the chunks HNSW index.

ADR-008: queries filter by embedding_model so vectors from different
embedding spaces are never compared.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class RetrievedChunk:
    id: str
    document_id: str
    content: str
    similarity: float
    metadata: dict[str, Any]
    rerank_score: float | None = None


async def ann_search(
    session: AsyncSession,
    tenant_id: str,
    query_embedding: list[float],
    embedding_model: str,
    top_k: int = 20,
) -> list[RetrievedChunk]:
    """Cosine ANN search via the chunks_embedding_idx HNSW index.

    Sets the RLS tenant context on this session before querying — the
    tenant_id WHERE clause is defense in depth on top of the policy.
    """
    await session.execute(text(f"SET app.tenant_id = '{tenant_id}'"))

    vector_literal = "[" + ",".join(f"{v:g}" for v in query_embedding) + "]"
    result = await session.execute(
        text(
            """
            SELECT id, document_id, content, metadata,
                   1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
            FROM chunks
            WHERE tenant_id = :tenant_id
              AND embedding_model = :embedding_model
            ORDER BY embedding <=> CAST(:query_vec AS vector)
            LIMIT :top_k
            """
        ),
        {
            "query_vec": vector_literal,
            "tenant_id": tenant_id,
            "embedding_model": embedding_model,
            "top_k": top_k,
        },
    )

    return [
        RetrievedChunk(
            id=str(row.id),
            document_id=str(row.document_id),
            content=row.content,
            similarity=float(row.similarity),
            metadata=dict(row.metadata or {}),
        )
        for row in result
    ]
