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
from ploy_agent.strategies.auto_disable import disabled_strategy_ids
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
        in_game = await conn.fetch(
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
        futures = await conn.fetch(
            """
            SELECT m.id AS market_id
            FROM markets m
            JOIN LATERAL (
              SELECT 1 FROM prices
              WHERE market_id = m.id AND mid IS NOT NULL
              ORDER BY ts DESC LIMIT 1
            ) lp ON true
            LEFT JOIN market_game_map mg ON mg.market_id = m.id
            WHERE m.status IS DISTINCT FROM 'closed'
              AND mg.market_id IS NULL
            """
        )
    seen: set[str] = set()
    out: list[str] = []
    for r in in_game:
        mid = str(r["market_id"])
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
    for r in futures:
        mid = str(r["market_id"])
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


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
            safe, reason = await resolution_gate(crit)
            await repo.set_resolution_cache(conn, market_id, safe, reason)
        else:
            safe, reason = cached
        if not safe:
            log.info("market_filtered_resolution", market_id=market_id, reason=reason)
            return

        gs = await repo.latest_market_game_state(conn, market_id)
        if not gs:
            gs = {}

        # Fetch book depth and spread for confidence scoring
        price_row = await conn.fetchrow(
            """
            SELECT depth_1c, bid, ask FROM prices
            WHERE market_id = $1 AND mid IS NOT NULL
            ORDER BY ts DESC LIMIT 1
            """,
            market_id,
        )
        depth_1c = float(price_row["depth_1c"] or 0) if price_row else 0.0
        spread = None
        if price_row and price_row["bid"] is not None and price_row["ask"] is not None:
            spread = float(price_row["ask"]) - float(price_row["bid"])

        ctx = StrategyContext(
            conn=conn, market_id=market_id, mrow=mrow, mid=mid,
            game_state=gs, model=model, http=http,
            depth_1c=depth_1c, spread=spread,
        )

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

    # Bounded concurrency: evaluate up to 8 markets in parallel
    sem = asyncio.Semaphore(8)
    # Mutable list so auto-disable can swap it each tick
    active_strategies: list[Any] = list(enabled)

    async def _eval_guarded(market_id: str) -> None:
        if stop.is_set():
            return
        async with sem:
            await _evaluate_market(
                pool, market_id, model, last_mid, last_eval, active_strategies, http
            )

    try:
        async with httpx.AsyncClient(verify=httpx_verify()) as http:
            while not stop.is_set():
                try:
                    # Auto-disable losing strategies
                    async with pool.acquire() as conn:
                        disabled = await disabled_strategy_ids(conn)
                    if disabled:
                        active_strategies[:] = [s for s in enabled if s.id not in disabled]
                        log.info("strategies_after_auto_disable",
                                 disabled=list(disabled),
                                 active=[s.id for s in active_strategies])
                    else:
                        active_strategies[:] = list(enabled)

                    market_ids = await _candidate_markets(pool)
                    tasks = [_eval_guarded(mid) for mid in market_ids]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for i, r in enumerate(results):
                        if isinstance(r, Exception):
                            log.warning("market_eval_failed", market_id=market_ids[i], error=str(r))
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
