from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import asyncpg

from ploy_agent.common.config import settings
from ploy_agent.common.logging_config import get_logger
from ploy_agent.notifier.rank import RankedPick, top_picks
from ploy_agent.sim import repo as sim_repo
from ploy_agent.sim.portfolio import ProfilePortfolio
from ploy_agent.sim.replay import load_final_mids, load_resolved_outcomes
from ploy_agent.sim.types import SimProfile, SimSignal

log = get_logger("sim.forward")


def _pick_to_signal(pick: RankedPick, ts: datetime) -> SimSignal:
    return SimSignal(
        ts=ts,
        market_id=pick.market_id,
        strategy_id=pick.strategy_id,
        category="unknown",
        question=pick.question,
        model_prob=pick.model_prob,
        market_prob=pick.market_prob,
        edge_cents=pick.edge_cents,
        confidence=pick.confidence,
        score=pick.score,
        end_date=pick.end_date,
    )


async def _load_open_into_portfolio(
    conn: asyncpg.Connection, pf: ProfilePortfolio
) -> None:
    rows = await conn.fetch(
        """
        SELECT id, market_id, strategy_id, category, question, direction,
               entry_price, opened_at, model_prob, confidence, edge_cents, score
        FROM sim_trades
        WHERE profile_id = $1 AND status = 'open'
        """,
        pf.profile.id,
    )
    from ploy_agent.sim.types import OpenPosition

    for r in rows:
        key = (pf.profile.id, str(r["market_id"]))
        pf.state.open_by_key[key] = OpenPosition(
            trade_id=int(r["id"]),
            profile_id=pf.profile.id,
            market_id=str(r["market_id"]),
            strategy_id=str(r["strategy_id"]),
            category=str(r["category"] or "unknown"),
            question=r["question"],
            direction=str(r["direction"]),
            entry_price=float(r["entry_price"]),
            opened_at=r["opened_at"],
            model_prob=float(r["model_prob"]),
            confidence=float(r["confidence"]),
            edge_cents=float(r["edge_cents"]),
            score=float(r["score"] or 0),
        )


async def _enrich_categories(
    conn: asyncpg.Connection, signals: list[SimSignal]
) -> list[SimSignal]:
    if not signals:
        return []
    ids = list({s.market_id for s in signals})
    rows = await conn.fetch(
        "SELECT id, category, question FROM markets WHERE id = ANY($1::text[])",
        ids,
    )
    meta = {str(r["id"]): (str(r["category"] or "unknown"), r["question"]) for r in rows}
    out: list[SimSignal] = []
    for s in signals:
        if s.market_id in meta:
            cat, q = meta[s.market_id]
            out.append(replace(s, category=cat, question=q or s.question))
        else:
            out.append(s)
    return out


async def forward_tick(
    conn: asyncpg.Connection,
    portfolios: dict[str, ProfilePortfolio],
    *,
    run_id: int,
    outcomes: dict[str, int],
) -> None:
    now = datetime.now(timezone.utc)
    picks = await top_picks(
        conn,
        limit=settings.rank_top_n * 3,
        strategy_ids=settings.strategy_ids(),
        merge_by_market=settings.rank_merge_by_market,
    )
    signals = await _enrich_categories(
        conn, [_pick_to_signal(p, now) for p in picks]
    )

    for sig in signals:
        resolved = sig.market_id in outcomes
        for pf in portfolios.values():
            key = (pf.profile.id, sig.market_id)

            # Skip opening new positions on already-resolved markets
            if resolved and key not in pf.state.open_by_key:
                continue

            closed = pf.process_signal(
                sig,
                market_resolved=resolved,
                resolved_outcome=outcomes.get(sig.market_id),
            )
            for ct in closed:
                await sim_repo.close_trade(conn, ct)

            pos = pf.state.open_by_key.get(key)
            if pos is not None and pos.trade_id is None:
                tid = await sim_repo.insert_open_trade(conn, run_id, pos)
                pos.trade_id = tid


async def run_forward(
    pool: asyncpg.Pool,
    profiles: list[SimProfile],
    stop: asyncio.Event,
    *,
    run_hours: float | None = None,
) -> None:
    hours = settings.sim_forward_run_hours if run_hours is None else run_hours
    started_at = datetime.now(timezone.utc)
    planned_end = started_at + timedelta(hours=hours) if hours > 0 else None
    notes = "live paper trading"
    if planned_end is not None:
        notes = f"{notes}; planned_end={planned_end.isoformat()}"

    async with pool.acquire() as conn:
        run_id = await sim_repo.create_run(conn, "forward", notes=notes)
    portfolios = {p.id: ProfilePortfolio(p) for p in profiles}

    async with pool.acquire() as conn:
        for pf in portfolios.values():
            await _load_open_into_portfolio(conn, pf)

    log.info(
        "sim_forward_started",
        run_id=run_id,
        profiles=len(profiles),
        run_hours=hours if hours > 0 else None,
        planned_end=planned_end.isoformat() if planned_end else None,
    )

    try:
        while not stop.is_set():
            if planned_end is not None and datetime.now(timezone.utc) >= planned_end:
                log.info(
                    "sim_forward_duration_reached",
                    run_id=run_id,
                    run_hours=hours,
                    planned_end=planned_end.isoformat(),
                )
                break
            try:
                outcomes = {}
                async with pool.acquire() as conn:
                    outcomes = await load_resolved_outcomes(conn)
                    await forward_tick(conn, portfolios, run_id=run_id, outcomes=outcomes)
            except Exception as e:
                log.warning("sim_forward_tick_failed", error=str(e))
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.sim_forward_interval_sec)
            except TimeoutError:
                pass
    finally:
        async with pool.acquire() as conn:
            final_mids = await load_final_mids(conn)
            now = datetime.now(timezone.utc)
            for pf in portfolios.values():
                for ct in pf.close_all_open(
                    now,
                    close_reason="forward_shutdown",
                    exit_prices=final_mids,
                ):
                    await sim_repo.close_trade(conn, ct)
            await sim_repo.finish_run(conn, run_id)
        log.info("sim_forward_stopped", run_id=run_id)
