from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_resolution_cache(conn: asyncpg.Connection, market_id: str) -> tuple[bool, str] | None:
    row = await conn.fetchrow(
        "SELECT is_safe, reason FROM market_resolution_cache WHERE market_id = $1", market_id
    )
    if not row:
        return None
    return bool(row["is_safe"]), str(row["reason"])


async def set_resolution_cache(conn: asyncpg.Connection, market_id: str, is_safe: bool, reason: str) -> None:
    await conn.execute(
        """
        INSERT INTO market_resolution_cache (market_id, is_safe, reason, checked_at)
        VALUES ($1,$2,$3,$4)
        ON CONFLICT (market_id) DO UPDATE SET
          is_safe = EXCLUDED.is_safe,
          reason = EXCLUDED.reason,
          checked_at = EXCLUDED.checked_at
        """,
        market_id,
        is_safe,
        reason,
        _utcnow(),
    )


async def latest_price_mid(conn: asyncpg.Connection, market_id: str) -> float | None:
    row = await conn.fetchrow(
        """
        SELECT mid FROM prices
        WHERE market_id = $1 AND mid IS NOT NULL
        ORDER BY ts DESC LIMIT 1
        """,
        market_id,
    )
    if not row or row["mid"] is None:
        return None
    return float(row["mid"])


async def mid_near_ts(
    conn: asyncpg.Connection, market_id: str, center: datetime, before_sec: float, after_sec: float
) -> tuple[float | None, float | None]:
    t0 = center - timedelta(seconds=before_sec)
    t1 = center + timedelta(seconds=after_sec)
    row_first = await conn.fetchrow(
        """
        SELECT mid FROM prices
        WHERE market_id = $1 AND mid IS NOT NULL AND ts >= $2 AND ts <= $3
        ORDER BY ts ASC LIMIT 1
        """,
        market_id,
        t0,
        t1,
    )
    row_last = await conn.fetchrow(
        """
        SELECT mid FROM prices
        WHERE market_id = $1 AND mid IS NOT NULL AND ts >= $2 AND ts <= $3
        ORDER BY ts DESC LIMIT 1
        """,
        market_id,
        t0,
        t1,
    )
    f = float(row_first["mid"]) if row_first and row_first["mid"] is not None else None
    l = float(row_last["mid"]) if row_last and row_last["mid"] is not None else None
    return f, l


async def price_move_range(
    conn: asyncpg.Connection, market_id: str, ts_from: datetime, ts_to: datetime
) -> float | None:
    row = await conn.fetchrow(
        """
        SELECT MAX(mid) - MIN(mid) AS rng FROM prices
        WHERE market_id = $1 AND mid IS NOT NULL AND ts >= $2 AND ts <= $3
        """,
        market_id,
        ts_from,
        ts_to,
    )
    if not row or row["rng"] is None:
        return None
    return float(row["rng"])


async def latest_market_game_state(conn: asyncpg.Connection, market_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT gs.home_score, gs.away_score, gs.period, gs.time_remaining, gs.possession,
               gs.home_team, gs.away_team, gs.game_id
        FROM market_game_map mg
        JOIN LATERAL (
          SELECT * FROM game_state
          WHERE game_id = mg.game_id
          ORDER BY ts DESC
          LIMIT 1
        ) gs ON true
        WHERE mg.market_id = $1
        LIMIT 1
        """,
        market_id,
    )
    if not row:
        return None
    return dict(row)


async def market_row(conn: asyncpg.Connection, market_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow("SELECT * FROM markets WHERE id = $1", market_id)


async def insert_fair_value(
    conn: asyncpg.Connection,
    *,
    market_id: str,
    strategy_id: str,
    model_prob: float,
    market_prob: float,
    edge_cents: float,
    confidence: float,
    reasoning: str,
    sources: list[dict[str, Any]],
    signal_json: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO fair_values (
          market_id, ts, strategy_id, model_prob, market_prob, edge_cents,
          confidence, reasoning, sources_json, signal_json
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb)
        """,
        market_id,
        _utcnow(),
        strategy_id,
        model_prob,
        market_prob,
        edge_cents,
        confidence,
        reasoning,
        json.dumps(sources),
        json.dumps(signal_json or {}),
    )


async def insert_game_event(
    conn: asyncpg.Connection,
    *,
    game_id: str,
    event_type: str,
    payload_json: dict[str, Any],
    home_score: int | None,
    away_score: int | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO game_events (game_id, ts, event_type, payload_json, home_score, away_score)
        VALUES ($1,$2,$3,$4::jsonb,$5,$6)
        """,
        game_id,
        _utcnow(),
        event_type,
        json.dumps(payload_json),
        home_score,
        away_score,
    )


async def latest_material_events(
    conn: asyncpg.Connection, game_id: str, within_sec: float
) -> list[asyncpg.Record]:
    cutoff = _utcnow() - timedelta(seconds=within_sec)
    return await conn.fetch(
        """
        SELECT * FROM game_events
        WHERE game_id = $1 AND ts > $2 AND event_type = 'score_swing'
        ORDER BY ts DESC
        LIMIT 5
        """,
        game_id,
        cutoff,
    )


async def recent_game_events(
    conn: asyncpg.Connection, game_id: str, within_sec: float
) -> list[asyncpg.Record]:
    cutoff = _utcnow() - timedelta(seconds=within_sec)
    return await conn.fetch(
        """
        SELECT * FROM game_events
        WHERE game_id = $1 AND ts > $2
        ORDER BY ts DESC
        LIMIT 20
        """,
        game_id,
        cutoff,
    )


async def sibling_market_mids(
    conn: asyncpg.Connection, gamma_event_id: str, exclude_market_id: str
) -> list[tuple[str, float, str | None]]:
    rows = await conn.fetch(
        """
        WITH lp AS (
          SELECT DISTINCT ON (market_id) market_id, mid
          FROM prices WHERE mid IS NOT NULL ORDER BY market_id, ts DESC
        )
        SELECT m.id AS market_id, lp.mid, m.question
        FROM markets m
        JOIN lp ON lp.market_id = m.id
        WHERE m.gamma_event_id = $1 AND m.id <> $2 AND m.status IS DISTINCT FROM 'closed'
        """,
        gamma_event_id,
        exclude_market_id,
    )
    return [(str(r["market_id"]), float(r["mid"]), r["question"]) for r in rows]


async def latest_lineup(conn: asyncpg.Connection, game_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT home_active, away_active FROM game_lineups
        WHERE game_id = $1 ORDER BY ts DESC LIMIT 1
        """,
        game_id,
    )
    if not row:
        return None
    ha = row["home_active"]
    aa = row["away_active"]
    return {
        "home_active": list(ha) if ha else [],
        "away_active": list(aa) if aa else [],
    }


async def player_deltas_for_keys(conn: asyncpg.Connection, keys: list[str]) -> float:
    if not keys:
        return 0.0
    val = await conn.fetchval(
        "SELECT COALESCE(SUM(epm_delta), 0) FROM player_impact WHERE player_key = ANY($1::text[])",
        keys,
    )
    return float(val or 0)
