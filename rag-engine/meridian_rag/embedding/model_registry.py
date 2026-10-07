"""Embedding model registry — lazy loading + version metadata (ADR-008)."""

import logging
from functools import lru_cache

from meridian_rag.models import EmbeddingMetadata

logger = logging.getLogger(__name__)

# Known models and their native output dimensions. Anything else is loaded
# and inspected at runtime.
_KNOWN_DIMS = {
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "sentence-transformers/all-mpnet-base-v2": 768,
    "BAAI/bge-large-en-v1.5": 1024,
}


class Embedder:
    """Wraps a sentence-transformers model, zero-padding output to storage_dim.

    Zero-padding preserves cosine similarity between vectors in the same
    embedding space (extra dimensions contribute 0 to both dot product and
    norms), so a 384-dim model can live in the schema's vector(1536) column.
    """

    def __init__(self, model_name: str, model_version: str, storage_dim: int) -> None:
        # Deferred import: torch + sentence-transformers are heavy and not
        # needed for unit tests of the surrounding pipeline.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        native_dim = self._model.get_sentence_embedding_dimension() or _KNOWN_DIMS.get(
            model_name, 0
        )
        if native_dim > storage_dim:
            raise ValueError(
                f"model {model_name} outputs {native_dim} dims, "
                f"larger than storage dim {storage_dim}"
            )
        self.metadata = EmbeddingMetadata(
            model_name=model_name,
            model_version=model_version,
            native_dim=native_dim,
            storage_dim=storage_dim,
        )
        logger.info(
            "embedder loaded",
            extra={"model": model_name, "native_dim": native_dim, "storage_dim": storage_dim},
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [pad_to_dim(list(map(float, v)), self.metadata.storage_dim) for v in vectors]


def pad_to_dim(vector: list[float], dim: int) -> list[float]:
    if len(vector) > dim:
        raise ValueError(f"vector has {len(vector)} dims, storage allows {dim}")
    return vector + [0.0] * (dim - len(vector))


@lru_cache(maxsize=4)
def get_embedder(model_name: str, model_version: str, storage_dim: int) -> Embedder:
    return Embedder(model_name, model_version, storage_dim)
