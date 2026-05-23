from __future__ import annotations

import asyncio
import signal
from typing import Any

import httpx

from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.common.ssl_utils import httpx_verify
from ploy_agent.ingestion import repo
from ploy_agent.ingestion.book_math import best_bid_ask_from_book, mid_from_ba
from ploy_agent.ingestion.clob import reconcile_book
from ploy_agent.ingestion.gamma import discover_markets, normalize_market_row
from ploy_agent.ingestion.links import rebuild_market_links

from .ws_market import run_market_ws

log = get_logger("ingestion")


async def _discover_and_upsert(client: httpx.AsyncClient, pool: Any) -> None:
    bundles = await discover_markets(client)
    async with pool.acquire() as conn:
        for b in bundles:
            row = normalize_market_row(b)
            if not row:
                continue
            await repo.upsert_market(conn, **row)
        await rebuild_market_links(conn)
    log.info("discovered_markets", n=len(bundles))


async def _snapshot_interval(pool: Any, client: httpx.AsyncClient, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=30.0)
            return
        except TimeoutError:
            pass
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, clob_asset_id FROM markets WHERE status IS DISTINCT FROM 'closed'"
            )
        for r in rows:
            if stop.is_set():
                return
            token = str(r["clob_asset_id"])
            mid = str(r["id"])
            try:
                bb, ba, m, depth, bids, asks = await reconcile_book(client, token)
                async with pool.acquire() as conn:
                    await repo.insert_order_book_snapshot(
                        conn,
                        market_id=mid,
                        bids=bids,
                        asks=asks,
                        trigger_reason="interval_30s",
                    )
                    await repo.insert_price_row(
                        conn,
                        market_id=mid,
                        bid=bb,
                        ask=ba,
                        mid=m,
                        depth_1c=depth,
                        volume_24h=None,
                        snapshot_kind="interval_30s",
                    )
            except Exception as e:
                log.warning("snapshot_failed", market_id=mid, error=str(e))


async def _rediscover_loop(client: httpx.AsyncClient, pool: Any, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=300.0)
            return
        except TimeoutError:
            pass
        try:
            await _discover_and_upsert(client, pool)
        except Exception as e:
            log.warning("rediscovery_failed", error=str(e))


async def _ws_supervisor(
    pool: Any,
    client: httpx.AsyncClient,
    on_update: Any,
    on_trade_snapshot: Any,
    stop: asyncio.Event,
) -> None:
    """Refresh WebSocket subscriptions periodically so new markets are picked up."""
    while not stop.is_set():
        async with pool.acquire() as conn:
            asset_to_market = await repo.load_markets_for_assets(conn)
        asset_ids = list(asset_to_market.keys())
        if not asset_ids:
            log.warning("no_assets_for_ws_sleeping")
            try:
                await asyncio.wait_for(stop.wait(), timeout=10.0)
            except TimeoutError:
                continue
            return
        local_stop = asyncio.Event()
        ws_task = asyncio.create_task(
            run_market_ws(asset_ids, asset_to_market, on_update, on_trade_snapshot, local_stop)
        )
        refresh = asyncio.create_task(asyncio.sleep(180))
        stop_wait = asyncio.create_task(stop.wait())
        await asyncio.wait(
            {ws_task, refresh, stop_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        local_stop.set()
        if not refresh.done():
            refresh.cancel()
        if not stop_wait.done():
            stop_wait.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
        if stop.is_set():
            return
        log.info("ws_refresh")


async def _run_ingestion(stop: asyncio.Event) -> None:
    configure_logging()
    pool = await get_pool()
    worker_stop = asyncio.Event()

    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        stop.set()
        worker_stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except (NotImplementedError, AttributeError):
            pass

    async def on_update(
        market_id: str,
        bb: float | None,
        ba: float | None,
        mid: float | None,
        depth: float | None,
    ) -> None:
        async with pool.acquire() as conn:
            await repo.insert_price_row(
                conn,
                market_id=market_id,
                bid=bb,
                ask=ba,
                mid=mid,
                depth_1c=depth,
                volume_24h=None,
                snapshot_kind="tick",
            )

    async def on_trade_snapshot(mid: str, bids: list, asks: list) -> None:
        async with pool.acquire() as conn:
            await repo.insert_order_book_snapshot(
                conn,
                market_id=mid,
                bids=bids,
                asks=asks,
                trigger_reason="trade",
            )
            bb, ba = best_bid_ask_from_book(bids, asks)
            m = mid_from_ba(bb, ba)
            await repo.insert_price_row(
                conn,
                market_id=mid,
                bid=bb,
                ask=ba,
                mid=m,
                depth_1c=None,
                volume_24h=None,
                snapshot_kind="trade",
            )

    try:
        async with httpx.AsyncClient(verify=httpx_verify()) as client:
            await _discover_and_upsert(client, pool)
            snap_task = asyncio.create_task(_snapshot_interval(pool, client, worker_stop))
            disc_task = asyncio.create_task(_rediscover_loop(client, pool, worker_stop))
            ws_sup = asyncio.create_task(
                _ws_supervisor(pool, client, on_update, on_trade_snapshot, worker_stop)
            )
            await stop.wait()
            worker_stop.set()
            for t in (snap_task, disc_task, ws_sup):
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
    finally:
        await close_pool()


def main() -> None:
    stop = asyncio.Event()

    async def runner() -> None:
        await _run_ingestion(stop)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
