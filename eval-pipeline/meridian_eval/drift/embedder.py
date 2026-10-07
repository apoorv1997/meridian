"""Output text embedder for semantic drift detection.

Uses the same sentence-transformers model configured for the RAG engine so
that drift embeddings are in the same vector space as document embeddings.
Model is loaded once and reused across calls.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class DriftEmbedder:
    """Thread-safe sentence-transformers embedder for drift detection."""

    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model = None

    def _load(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # lazy import
            self._model = SentenceTransformer(self._model_name)
            logger.info("drift embedder loaded", extra={"model": self._model_name})

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        """Blocking embed — run via asyncio.to_thread in async contexts."""
        self._load()
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vectors]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Async embed — offloads CPU-bound inference to a thread."""
        return await asyncio.to_thread(self.embed_sync, texts)
