"""Embedding model name + version tracking (ADR-008)."""

from pydantic import BaseModel


class EmbeddingMetadata(BaseModel):
    """Identifies an embedding space.

    Vectors from different (model_name, model_version) pairs live in
    different spaces and must never be compared. The storage dimension is
    fixed by the schema (vector(1536)); shorter model outputs are zero-padded,
    which preserves cosine similarity within the same space.
    """

    model_name: str
    model_version: str
    native_dim: int
    storage_dim: int = 1536

    @property
    def key(self) -> str:
        return f"{self.model_name}@{self.model_version}"
