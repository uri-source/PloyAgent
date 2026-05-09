from __future__ import annotations

import asyncio
import signal

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.notifier import repo as rec_repo
from ploy_agent.notifier.rank import top_picks

log = get_logger("notifier")


async def _tick(pool) -> None:
    async with pool.acquire() as conn:
        picks = await top_picks(
            conn,
            limit=settings.rank_top_n,
            strategy_ids=settings.strategy_ids(),
            merge_by_market=settings.rank_merge_by_market,
        )
    if not picks:
        log.info("no_picks")
        return
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
            await rec_repo.insert_recommendation(
                conn,
                market_id=p.market_id,
                score=p.score,
                payload=payload,
                strategy_id=p.strategy_id,
            )
    log.info("recommendations_persisted", n=len(picks))


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
        while not stop.is_set():
            try:
                await _tick(pool)
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
