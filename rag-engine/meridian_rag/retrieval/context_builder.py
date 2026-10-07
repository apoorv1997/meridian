"""Assemble retrieved chunks into prompt context for the gateway."""

from dataclasses import dataclass

from meridian_rag.retrieval.pgvector import RetrievedChunk


@dataclass
class RetrievalContext:
    text: str
    document_ids: list[str]  # for gateway cache-entry tagging (invalidation)
    chunk_count: int


def build_context(
    chunks: list[RetrievedChunk],
    max_chars: int = 8000,
    separator: str = "\n\n---\n\n",
) -> RetrievalContext:
    """Concatenate ranked chunks into a single context block, best-first,
    stopping before max_chars is exceeded.

    Returns the source document IDs so the gateway can tag its cache entry —
    when any of those documents update, the cache entry is invalidated.
    """
    parts: list[str] = []
    document_ids: list[str] = []
    used = 0

    for chunk in chunks:
        addition = len(chunk.content) + (len(separator) if parts else 0)
        if used + addition > max_chars:
            break
        parts.append(chunk.content)
        used += addition
        if chunk.document_id not in document_ids:
            document_ids.append(chunk.document_id)

    return RetrievalContext(
        text=separator.join(parts),
        document_ids=document_ids,
        chunk_count=len(parts),
    )
