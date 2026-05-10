from __future__ import annotations

import asyncio
import signal

import httpx

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.notifier import repo as rec_repo
from ploy_agent.notifier.rank import RankedPick, top_picks
from ploy_agent.notifier.slack import post_picks

log = get_logger("notifier")


async def _recently_notified(pool, market_ids: list[str]) -> set[str]:
    if not market_ids:
        return set()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT market_id FROM recommendations
            WHERE market_id = ANY($1::text[])
              AND ts > NOW() - INTERVAL '15 minutes'
              AND status = 'pending'
            """,
            market_ids,
        )
    return {str(r["market_id"]) for r in rows}


async def _tick(pool, http: httpx.AsyncClient) -> None:
    async with pool.acquire() as conn:
        picks = await top_picks(
            conn,
            limit=settings.rank_top_n * 2,
            strategy_ids=settings.strategy_ids(),
            merge_by_market=settings.rank_merge_by_market,
        )
    if not picks:
        log.info("no_picks")
        return

    already = await _recently_notified(pool, [p.market_id for p in picks])
    picks = [p for p in picks if p.market_id not in already][:settings.rank_top_n]
    if not picks:
        log.info("no_new_picks")
        return

    pick_ids: list[tuple[RankedPick, int]] = []
    async with pool.acquire() as conn:
        for p in picks:
            payload = {
                "strategy_id": p.strategy_id,
                "edge_cents": p.edge_cents,
                "model_prob": p.model_prob,
                "market_prob": p.market_prob,
                "confidence": p.confidence,
                "reasoning": p.reasoning,
            }
            rec_id = await rec_repo.insert_recommendation(
                conn,
                market_id=p.market_id,
                score=p.score,
                payload=payload,
                strategy_id=p.strategy_id,
            )
            pick_ids.append((p, rec_id))
    log.info("recommendations_persisted", n=len(pick_ids))

    if settings.slack_bot_token and settings.slack_channel:
        refs = await post_picks(http, pick_ids)
        if refs:
            async with pool.acquire() as conn:
                for rec_id, channel, ts in refs:
                    await rec_repo.update_slack_refs(conn, rec_id, channel, ts)


async def _run(stop: asyncio.Event) -> None:
    configure_logging()
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
        async with httpx.AsyncClient() as http:
            while not stop.is_set():
                try:
                    await _tick(pool, http)
                except Exception as e:
                    log.warning("notifier_tick_failed", error=str(e))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60.0)
                except TimeoutError:
                    pass
    finally:
        await close_pool()


def main() -> None:
    stop = asyncio.Event()
    asyncio.run(_run(stop))


if __name__ == "__main__":
    main()
