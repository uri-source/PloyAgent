from __future__ import annotations

import asyncpg


async def schema_ready(conn: asyncpg.Connection) -> bool:
    """True if migration 007 kalshi / cross-venue tables exist."""
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.tables
              WHERE table_schema = 'public' AND table_name = 'cross_venue_pairs'
            )
            """
        )
    )
