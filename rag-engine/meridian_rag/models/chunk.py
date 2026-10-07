"""Chunk dataclass — the unit of storage and retrieval.

ADR-008: every chunk carries embedding_model + embedding_version so queries
can filter by model and never compare vectors across embedding spaces.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    tenant_id: str
    document_id: str
    content: str
    content_hash: str
    chunk_index: int
    embedding_model: str
    embedding_version: str
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if not self.embedding_model or not self.embedding_version:
            raise ValueError(
                "ADR-008 violation: chunk requires embedding_model and embedding_version"
            )
