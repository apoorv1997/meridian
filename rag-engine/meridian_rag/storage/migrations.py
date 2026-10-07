"""Schema verification wrapper.

The authoritative schema lives in infra/docker/postgres/init.sql and is
applied by the postgres container at first boot. This module verifies at
service startup that the tables this service depends on actually exist —
fail fast instead of erroring on the first message.

Alembic migrations can be layered here in a later phase if the schema starts
evolving independently of init.sql.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

REQUIRED_TABLES = ("chunks", "cache_entries", "tenants")


async def verify_schema(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Raise RuntimeError if any required table is missing."""
    async with sessions() as session:
        result = await session.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = ANY(:tables)"
            ),
            {"tables": list(REQUIRED_TABLES)},
        )
        found = {row[0] for row in result}

    missing = set(REQUIRED_TABLES) - found
    if missing:
        raise RuntimeError(
            f"schema verification failed: missing tables {sorted(missing)}; "
            "apply infra/docker/postgres/init.sql first"
        )
    logger.info("schema verified", extra={"tables": sorted(found)})
