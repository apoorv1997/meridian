"""Environment-driven configuration. Fail fast on missing required vars."""

import os
from dataclasses import dataclass, field


def _require(key: str) -> str:
    value = os.environ.get(key, "")
    if not value:
        raise RuntimeError(f"config: required environment variable {key!r} is not set")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _require("DATABASE_URL"))
    kafka_brokers: str = field(
        default_factory=lambda: os.environ.get("KAFKA_BROKERS", "localhost:9094")
    )
    documents_topic: str = field(
        default_factory=lambda: os.environ.get("KAFKA_TOPIC_DOCUMENTS", "meridian.documents")
    )
    dlq_topic: str = field(
        default_factory=lambda: os.environ.get("KAFKA_TOPIC_DLQ", "meridian.dlq")
    )
    consumer_group: str = field(
        default_factory=lambda: os.environ.get("RAG_CONSUMER_GROUP", "meridian-rag")
    )

    embedding_model: str = field(
        default_factory=lambda: os.environ.get(
            "RAG_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )
    embedding_version: str = field(
        default_factory=lambda: os.environ.get("RAG_EMBEDDING_VERSION", "1")
    )
    storage_dim: int = field(
        default_factory=lambda: int(os.environ.get("RAG_STORAGE_DIM", "1536"))
    )

    chunk_strategy: str = field(
        default_factory=lambda: os.environ.get("RAG_CHUNK_STRATEGY", "semantic")
    )
    chunk_size: int = field(default_factory=lambda: int(os.environ.get("RAG_CHUNK_SIZE", "1000")))
    chunk_overlap: int = field(
        default_factory=lambda: int(os.environ.get("RAG_CHUNK_OVERLAP", "200"))
    )

    worker_count: int = field(default_factory=lambda: int(os.environ.get("RAG_WORKERS", "4")))
    queue_max_size: int = field(
        default_factory=lambda: int(os.environ.get("RAG_QUEUE_MAX", "256"))
    )
    write_p99_threshold_ms: float = field(
        default_factory=lambda: float(os.environ.get("RAG_WRITE_P99_MS", "200"))
    )

    max_dlq_retries: int = field(
        default_factory=lambda: int(os.environ.get("RAG_MAX_DLQ_RETRIES", "3"))
    )


def load_settings() -> Settings:
    return Settings()
