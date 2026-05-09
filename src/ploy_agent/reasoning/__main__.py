from __future__ import annotations

import asyncio
import signal
import time
from typing import Any

import httpx

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.common.ssl_utils import httpx_verify
from ploy_agent.reasoning import repo
from ploy_agent.reasoning.model import load_model
from ploy_agent.reasoning.resolution import resolution_gate
from ploy_agent.strategies import get_enabled
from ploy_agent.strategies.types import StrategyContext

log = get_logger("reasoning")


async def _hydrate_last_mid(pool: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (market_id) market_id, mid
            FROM prices
            WHERE mid IS NOT NULL
            ORDER BY market_id, ts DESC
            """
        )
    for r in rows:
        out[str(r["market_id"])] = float(r["mid"])
    return out


async def _candidate_markets(pool: Any) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT mg.market_id
            FROM market_game_map mg
            JOIN LATERAL (
              SELECT 1 FROM game_state
              WHERE game_id = mg.game_id AND ts > NOW() - INTERVAL '6 hours'
              LIMIT 1
            ) gs ON true
            """
        )
    return [str(r["market_id"]) for r in rows]


async def _evaluate_market(
    pool: Any,
    market_id: str,
    model: dict[str, Any],
    last_mid: dict[str, float],
    last_eval: dict[str, float],
    enabled: list[Any],
    http: httpx.AsyncClient,
) -> None:
    now = time.monotonic()
    async with pool.acquire() as conn:
        mid = await repo.latest_price_mid(conn, market_id)
        if mid is None:
            return
        prev = last_mid.get(market_id)
        moved = prev is None or abs(mid - prev) >= 0.02
        due = (now - last_eval.get(market_id, 0.0)) >= 60.0
        if not moved and not due:
            return
        last_mid[market_id] = mid
        last_eval[market_id] = now

        mrow = await repo.market_row(conn, market_id)
        if not mrow:
            return
        crit = mrow.get("resolution_criteria")

        cached = await repo.get_resolution_cache(conn, market_id)
        if cached is None:
            safe, reason = await asyncio.to_thread(resolution_gate, crit)
            await repo.set_resolution_cache(conn, market_id, safe, reason)
        else:
            safe, reason = cached
        if not safe:
            log.info("market_filtered_resolution", market_id=market_id, reason=reason)
            return

        gs = await repo.latest_market_game_state(conn, market_id)
        if not gs:
            return

        ctx = StrategyContext(conn=conn, market_id=market_id, mrow=mrow, mid=mid, game_state=gs, model=model, http=http)

        for strat in enabled:
            try:
                result = await strat.run(ctx)
            except Exception as e:
                log.warning(
                    "strategy_failed",
                    strategy=getattr(type(strat), "id", type(strat).__name__),
                    market_id=market_id,
                    error=str(e),
                )
                continue
            if result is None:
                continue
            await repo.insert_fair_value(
                conn,
                market_id=market_id,
                strategy_id=getattr(type(strat), "id", type(strat).__name__),
                model_prob=result.model_prob,
                market_prob=result.market_prob,
                edge_cents=result.edge_cents,
                confidence=result.confidence,
                reasoning=result.reasoning,
                sources=result.sources,
                signal_json=result.signal_json,
            )
            log.info(
                "fair_value_written",
                market_id=market_id,
                strategy=getattr(type(strat), "id", type(strat).__name__),
                edge=result.edge_cents,
                conf=result.confidence,
            )


async def _run(stop: asyncio.Event) -> None:
    configure_logging()
    pool = await get_pool()
    model = load_model()
    last_mid = await _hydrate_last_mid(pool)
    last_eval: dict[str, float] = {}
    enabled = get_enabled(settings)
    log.info("strategies_enabled", ids=[s.id for s in enabled])

    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except (NotImplementedError, AttributeError):
            pass

    try:
        async with httpx.AsyncClient(verify=httpx_verify()) as http:
            while not stop.is_set():
                try:
                    mids = await _candidate_markets(pool)
                    for mid in mids:
                        if stop.is_set():
                            break
                        await _evaluate_market(pool, mid, model, last_mid, last_eval, enabled, http)
                except Exception as e:
                    log.warning("reasoning_tick_failed", error=str(e))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2.0)
                except TimeoutError:
                    pass
    finally:
        await close_pool()


def main() -> None:
    stop = asyncio.Event()
    asyncio.run(_run(stop))


if __name__ == "__main__":
    main()
