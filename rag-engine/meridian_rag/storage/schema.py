"""SQLAlchemy models mapping the tables created by infra/docker/postgres/init.sql.

The schema (including RLS policies and HNSW indexes) is owned by init.sql —
these models are mappings, not the source of truth.
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STORAGE_DIM = 1536


class Base(DeclarativeBase):
    pass


class ChunkRow(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    document_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(STORAGE_DIM))
    embedding_model: Mapped[str] = mapped_column(String, nullable=False)
    embedding_version: Mapped[str] = mapped_column(String, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


def session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory. Accepts postgresql:// URLs and
    upgrades them to the asyncpg driver."""
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    # asyncpg rejects libpq-style query params like sslmode
    database_url = database_url.split("?")[0]
    engine = create_async_engine(database_url, pool_size=10, max_overflow=5)
    return async_sessionmaker(engine, expire_on_commit=False)
