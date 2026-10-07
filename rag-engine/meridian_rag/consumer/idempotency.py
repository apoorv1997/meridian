"""Content-hash idempotency — re-ingesting the same document version is a no-op."""

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meridian_rag.storage.schema import ChunkRow


def content_hash(content: str) -> str:
    """SHA256 of the document content; stored on every chunk row."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def is_duplicate(
    session: AsyncSession,
    tenant_id: str,
    document_id: str,
    doc_hash: str,
) -> bool:
    """True when chunks for this exact content version already exist.

    The consumer commits the offset and skips processing on a duplicate —
    same doc + same content = no-op, per the ingestion contract.
    """
    stmt = (
        select(ChunkRow.id)
        .where(
            ChunkRow.tenant_id == tenant_id,
            ChunkRow.document_id == document_id,
            ChunkRow.content_hash == doc_hash,
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.first() is not None
