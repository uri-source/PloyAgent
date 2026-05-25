from __future__ import annotations

from typing import Any

import asyncpg

from ploy_agent.sim.types import ClosedTrade, OpenPosition, SimProfile


def row_to_profile(row: asyncpg.Record) -> SimProfile:
    sids = row.get("strategy_ids")
    return SimProfile(
        id=str(row["id"]),
        min_edge_cents=float(row["min_edge_cents"]),
        min_confidence=float(row["min_confidence"]),
        min_model_prob=float(row["min_model_prob"]),
        strategy_ids=tuple(sids) if sids else (),
        max_open_per_market=int(row["max_open_per_market"]),
        cooldown_sec=int(row["cooldown_sec"]),
    )


async def list_profiles(
    conn: asyncpg.Connection, profile_ids: list[str] | None = None
) -> list[SimProfile]:
    if profile_ids:
        rows = await conn.fetch(
            "SELECT * FROM sim_profiles WHERE id = ANY($1::text[]) ORDER BY id",
            profile_ids,
        )
    else:
        rows = await conn.fetch("SELECT * FROM sim_profiles ORDER BY id")
    return [row_to_profile(r) for r in rows]


async def upsert_profile(conn: asyncpg.Connection, profile: SimProfile) -> None:
    sids = list(profile.strategy_ids) if profile.strategy_ids else None
    await conn.execute(
        """
        INSERT INTO sim_profiles (
          id, min_edge_cents, min_confidence, min_model_prob,
          strategy_ids, max_open_per_market, cooldown_sec
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (id) DO UPDATE SET
          min_edge_cents = EXCLUDED.min_edge_cents,
          min_confidence = EXCLUDED.min_confidence,
          min_model_prob = EXCLUDED.min_model_prob,
          strategy_ids = EXCLUDED.strategy_ids,
          max_open_per_market = EXCLUDED.max_open_per_market,
          cooldown_sec = EXCLUDED.cooldown_sec
        """,
        profile.id,
        profile.min_edge_cents,
        profile.min_confidence,
        profile.min_model_prob,
        sids,
        profile.max_open_per_market,
        profile.cooldown_sec,
    )


async def create_run(conn: asyncpg.Connection, mode: str, notes: str | None = None) -> int:
    return int(
        await conn.fetchval(
            """
            INSERT INTO sim_runs (mode, notes) VALUES ($1, $2) RETURNING id
            """,
            mode,
            notes,
        )
    )


async def finish_run(conn: asyncpg.Connection, run_id: int) -> None:
    await conn.execute(
        "UPDATE sim_runs SET ended_at = NOW() WHERE id = $1",
        run_id,
    )


async def insert_open_trade(
    conn: asyncpg.Connection,
    run_id: int | None,
    pos: OpenPosition,
) -> int:
    return int(
        await conn.fetchval(
            """
            INSERT INTO sim_trades (
              sim_run_id, profile_id, market_id, strategy_id, category, question,
              opened_at, status, direction, entry_price, model_prob, confidence,
              edge_cents, score
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,'open',$8,$9,$10,$11,$12,$13)
            RETURNING id
            """,
            run_id,
            pos.profile_id,
            pos.market_id,
            pos.strategy_id,
            pos.category,
            pos.question,
            pos.opened_at,
            pos.direction,
            pos.entry_price,
            pos.model_prob,
            pos.confidence,
            pos.edge_cents,
            pos.score,
        )
    )


async def close_trade(conn: asyncpg.Connection, trade: ClosedTrade) -> None:
    if trade.trade_id is None:
        return
    await conn.execute(
        """
        UPDATE sim_trades SET
          closed_at = $2, status = 'closed', exit_price = $3,
          resolved_outcome = $4, pnl_cents = $5, close_reason = $6
        WHERE id = $1
        """,
        trade.trade_id,
        trade.closed_at,
        trade.exit_price,
        trade.resolved_outcome,
        trade.pnl_cents,
        trade.close_reason,
    )


async def fetch_trades(
    conn: asyncpg.Connection,
    *,
    profile_id: str | None = None,
    sim_run_id: int | None = None,
    status: str | None = None,
    limit: int = 5000,
) -> list[asyncpg.Record]:
    clauses = ["1=1"]
    args: list[Any] = []
    n = 1
    if profile_id:
        clauses.append(f"profile_id = ${n}")
        args.append(profile_id)
        n += 1
    if sim_run_id is not None:
        clauses.append(f"sim_run_id = ${n}")
        args.append(sim_run_id)
        n += 1
    if status:
        clauses.append(f"status = ${n}")
        args.append(status)
        n += 1
    args.append(limit)
    q = f"""
        SELECT * FROM sim_trades
        WHERE {' AND '.join(clauses)}
        ORDER BY opened_at DESC
        LIMIT ${n}
    """
    return await conn.fetch(q, *args)


async def clear_trades_for_run(conn: asyncpg.Connection, run_id: int) -> None:
    await conn.execute("DELETE FROM sim_trades WHERE sim_run_id = $1", run_id)


async def resolve_forward_run_id(
    conn: asyncpg.Connection, sim_run_id: int | None = None
) -> int | None:
    """Explicit run id, else latest forward run (active preferred). None if no forward run."""
    if sim_run_id is not None:
        return sim_run_id
    row = await fetch_latest_forward_run(conn)
    return int(row["id"]) if row else None


async def fetch_latest_forward_run(conn: asyncpg.Connection) -> asyncpg.Record | None:
    """Most recent forward run (active first, else last finished)."""
    return await conn.fetchrow(
        """
        SELECT id, started_at, ended_at, mode, notes
        FROM sim_runs
        WHERE mode = 'forward'
        ORDER BY ended_at NULLS FIRST, started_at DESC
        LIMIT 1
        """
    )


async def fetch_run_totals(conn: asyncpg.Connection, run_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT
          COUNT(*)::int AS total_trades,
          COUNT(*) FILTER (WHERE status = 'open')::int AS open,
          COUNT(*) FILTER (WHERE status = 'closed')::int AS closed,
          COUNT(*) FILTER (WHERE status = 'open' AND UPPER(direction) = 'BUY')::int AS open_buys,
          COUNT(*) FILTER (WHERE status = 'open' AND UPPER(direction) = 'SELL')::int AS open_sells,
          COUNT(*) FILTER (WHERE status = 'closed' AND UPPER(direction) = 'BUY')::int AS closed_buys,
          COUNT(*) FILTER (WHERE status = 'closed' AND UPPER(direction) = 'SELL')::int AS closed_sells,
          COALESCE(SUM(pnl_cents) FILTER (WHERE status = 'closed'), 0)::float AS total_pnl_cents,
          COUNT(*) FILTER (WHERE opened_at > NOW() - INTERVAL '24 hours')::int AS opened_24h,
          COUNT(*) FILTER (
            WHERE status = 'closed' AND closed_at > NOW() - INTERVAL '24 hours'
          )::int AS closed_24h
        FROM sim_trades
        WHERE sim_run_id = $1
        """,
        run_id,
    )


async def fetch_recent_trades_for_run(
    conn: asyncpg.Connection,
    run_id: int,
    *,
    limit: int = 20,
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT *,
               COALESCE(closed_at, opened_at) AS event_at
        FROM sim_trades
        WHERE sim_run_id = $1
        ORDER BY COALESCE(closed_at, opened_at) DESC
        LIMIT $2
        """,
        run_id,
        limit,
    )
