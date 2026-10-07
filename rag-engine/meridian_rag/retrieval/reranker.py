"""Cross-encoder reranking.

ANN search optimizes for recall (top-20 candidates); the cross-encoder scores
each (query, chunk) pair jointly for much better precision, and we keep the
top-5. Two-stage retrieval: cheap-and-wide, then expensive-and-narrow.
"""

import asyncio
import logging
from functools import lru_cache

from meridian_rag.retrieval.pgvector import RetrievedChunk

logger = logging.getLogger(__name__)

DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@lru_cache(maxsize=2)
def _load_cross_encoder(model_name: str):  # noqa: ANN202 — heavy import deferred
    from sentence_transformers import CrossEncoder

    logger.info("cross-encoder loaded", extra={"model": model_name})
    return CrossEncoder(model_name)


class Reranker:
    def __init__(self, model_name: str = DEFAULT_RERANK_MODEL) -> None:
        self._model_name = model_name

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedChunk],
        top_n: int = 5,
    ) -> list[RetrievedChunk]:
        """Score candidates against the query and return the top_n by score."""
        if not candidates:
            return []
        if len(candidates) <= top_n:
            top_n = len(candidates)

        model = _load_cross_encoder(self._model_name)
        pairs = [(query, c.content) for c in candidates]
        # CPU-bound inference off the event loop.
        scores = await asyncio.to_thread(model.predict, pairs)

        for candidate, score in zip(candidates, scores, strict=True):
            candidate.rerank_score = float(score)

        ranked = sorted(candidates, key=lambda c: c.rerank_score or 0.0, reverse=True)
        return ranked[:top_n]
