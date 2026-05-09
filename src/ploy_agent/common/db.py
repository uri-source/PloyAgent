from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from ploy_agent.common.config import settings


def dsn() -> str:
    return os.environ.get("DATABASE_URL") or settings.database_url


_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn(), min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def pool_lifespan() -> AsyncIterator[asyncpg.Pool]:
    p = await get_pool()
    try:
        yield p
    finally:
        await close_pool()
