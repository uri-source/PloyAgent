from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone
from typing import Any

import httpx

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.kalshi import client, repo
from ploy_agent.kalshi.schema import schema_ready

log = get_logger("kalshi.ingest")


async def _poll_ticker(
    conn: Any,
    http: httpx.AsyncClient,
    ticker: str,
) -> None:
    meta = await client.fetch_market(http, ticker)
    close_time = None
    if meta:
        ct = meta.get("close_time") or meta.get("expiration_time")
        if ct:
            try:
                close_time = datetime.fromisoformat(str(ct).replace("Z", "+00:00"))
            except ValueError:
                close_time = None
        await repo.upsert_kalshi_market(
            conn,
            ticker=ticker,
            title=meta.get("title"),
            status=meta.get("status"),
            series_ticker=meta.get("series_ticker"),
            close_time=close_time,
        )

    ob = await client.fetch_orderbook(http, ticker)
    if not ob:
        return
    bid, ask, mid, depth = client.parse_orderbook(ob)
    if mid is None:
        return
    await repo.insert_kalshi_price(
        conn,
        ticker=ticker,
        ts=datetime.now(timezone.utc),
        bid=bid,
        ask=ask,
        mid=mid,
        depth_1c=depth,
    )


async def _poll_once(pool: Any, http: httpx.AsyncClient) -> None:
    async with pool.acquire() as conn:
        if not await schema_ready(conn):
            log.info("kalshi_poll_skip", reason="schema_not_ready")
            return
        pairs = await repo.active_pairs(conn)
        tickers = sorted({str(p["kalshi_ticker"]) for p in pairs})
    if not tickers:
        log.info("kalshi_poll_skip", reason="no_active_pairs")
        return
    for ticker in tickers:
        try:
            async with pool.acquire() as conn:
                await _poll_ticker(conn, http, ticker)
        except Exception as e:
            log.warning("kalshi_poll_ticker_failed", ticker=ticker, error=str(e))


async def _run(stop: asyncio.Event) -> None:
    configure_logging()
    if not settings.kalshi_enabled:
        log.info("kalshi_ingest_disabled")
        return
    pool = await get_pool()
    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except (NotImplementedError, AttributeError):
            pass

    try:
        async with client.new_http_client() as http:
            while not stop.is_set():
                try:
                    await _poll_once(pool, http)
                except Exception as e:
                    log.warning("kalshi_poll_failed", error=str(e))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.kalshi_poll_interval_sec)
                except TimeoutError:
                    pass
    finally:
        await close_pool()


def main() -> None:
    stop = asyncio.Event()
    asyncio.run(_run(stop))


if __name__ == "__main__":
    main()
