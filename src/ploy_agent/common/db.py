from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from ploy_agent.common.config import settings


def dsn() -> str:
    return settings.database_url


_pool: asyncpg.Pool | None = None
_pool_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Lazily create lock in the running event loop."""
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _get_lock():
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
