from __future__ import annotations

from datetime import datetime, timezone
import asyncpg

from ploy_agent.common.pnl import outcome_from_final_mid
from ploy_agent.common.scoring import composite_score, hours_until
from ploy_agent.sim import repo as sim_repo
from ploy_agent.sim.portfolio import ProfilePortfolio
from ploy_agent.sim.types import SimProfile, SimSignal


async def load_resolved_outcomes(conn: asyncpg.Connection) -> dict[str, int]:
    rows = await conn.fetch(
        """
        WITH final_prices AS (
          SELECT DISTINCT ON (market_id) market_id, mid
          FROM prices
          WHERE mid IS NOT NULL
          ORDER BY market_id, ts DESC
        )
        SELECT fp.market_id, fp.mid, m.status
        FROM final_prices fp
        JOIN markets m ON m.id = fp.market_id
        WHERE m.status = 'closed'
           OR (m.end_date IS NOT NULL AND m.end_date < NOW() - INTERVAL '30 minutes')
        """
    )
    out: dict[str, int] = {}
    for r in rows:
        oc = outcome_from_final_mid(float(r["mid"]))
        if oc is not None:
            out[str(r["market_id"])] = oc
    return out


async def load_final_mids(conn: asyncpg.Connection) -> dict[str, float]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (market_id) market_id, mid
        FROM prices
        WHERE mid IS NOT NULL
        ORDER BY market_id, ts DESC
        """
    )
    return {str(r["market_id"]): float(r["mid"]) for r in rows if r["mid"] is not None}


def _row_to_signal(row: asyncpg.Record, now: datetime) -> SimSignal:
    end = row.get("end_date")
    if isinstance(end, datetime) and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    h = hours_until(end, now) if end else 24.0
    edge = float(row["edge_cents"])
    conf = float(row["confidence"])
    depth = float(row.get("depth_1c") or 0.0)
    market_prob = float(row["market_prob"])
    sc = composite_score(edge, depth, conf, h, market_mid=market_prob)
    ts = row["ts"]
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return SimSignal(
        ts=ts,
        market_id=str(row["market_id"]),
        strategy_id=str(row["strategy_id"]),
        category=str(row.get("category") or "unknown"),
        question=row.get("question"),
        model_prob=float(row["model_prob"]),
        market_prob=float(row["market_prob"]),
        edge_cents=edge,
        confidence=conf,
        score=sc,
    )


async def run_replay(
    conn: asyncpg.Connection,
    *,
    from_ts: datetime,
    to_ts: datetime,
    profiles: list[SimProfile],
    notes: str | None = None,
) -> int:
    run_id = await sim_repo.create_run(conn, "replay", notes=notes)
    outcomes = await load_resolved_outcomes(conn)
    final_mids = await load_final_mids(conn)

    rows = await conn.fetch(
        """
        SELECT f.ts, f.market_id, f.strategy_id, f.model_prob, f.market_prob,
               f.edge_cents, f.confidence, m.category, m.question, m.end_date,
               COALESCE(lp.depth_1c, 0) AS depth_1c
        FROM fair_values f
        JOIN markets m ON m.id = f.market_id
        LEFT JOIN LATERAL (
          SELECT depth_1c FROM prices p
          WHERE p.market_id = f.market_id AND p.ts <= f.ts AND p.depth_1c IS NOT NULL
          ORDER BY p.ts DESC LIMIT 1
        ) lp ON TRUE
        WHERE f.ts >= $1 AND f.ts <= $2
        ORDER BY f.ts ASC
        """,
        from_ts,
        to_ts,
    )

    portfolios = {p.id: ProfilePortfolio(p) for p in profiles}

    for row in rows:
        signal = _row_to_signal(row, to_ts)
        mid = signal.market_id
        resolved = mid in outcomes
        outcome = outcomes.get(mid)

        for pf in portfolios.values():
            key = (pf.profile.id, mid)

            # Skip opening new positions on already-resolved markets
            # (only process if we already have an open position to close)
            if resolved and key not in pf.state.open_by_key:
                continue

            closed = pf.process_signal(
                signal,
                market_resolved=resolved,
                resolved_outcome=outcome,
            )
            for ct in closed:
                await sim_repo.close_trade(conn, ct)

            pos = pf.state.open_by_key.get(key)
            if pos is not None and pos.trade_id is None:
                pos.trade_id = await sim_repo.insert_open_trade(conn, run_id, pos)

    for pf in portfolios.values():
        for ct in pf.close_all_open(
            to_ts,
            close_reason="mark_to_market",
            exit_prices=final_mids,
        ):
            await sim_repo.close_trade(conn, ct)

    await sim_repo.finish_run(conn, run_id)
    return run_id
