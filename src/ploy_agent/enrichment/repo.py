from __future__ import annotations

import json
from datetime import datetime, timezone
import asyncpg


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def fetch_latest_scores(conn: asyncpg.Connection, game_id: str) -> tuple[int, int] | None:
    row = await conn.fetchrow(
        """
        SELECT home_score, away_score FROM game_state
        WHERE game_id = $1 ORDER BY ts DESC LIMIT 1
        """,
        game_id,
    )
    if not row:
        return None
    return int(row["home_score"] or 0), int(row["away_score"] or 0)


async def insert_game_lineups(
    conn: asyncpg.Connection,
    *,
    game_id: str,
    home_active: list[str],
    away_active: list[str],
) -> None:
    await conn.execute(
        """
        INSERT INTO game_lineups (game_id, ts, home_active, away_active)
        VALUES ($1,$2,$3::jsonb,$4::jsonb)
        """,
        game_id,
        _utcnow(),
        json.dumps(home_active),
        json.dumps(away_active),
    )


async def upsert_market_game_map(conn: asyncpg.Connection, market_id: str, game_id: str) -> None:
    await conn.execute(
        """
        INSERT INTO market_game_map (market_id, game_id, source, updated_at)
        VALUES ($1,$2,'auto',$3)
        ON CONFLICT (market_id) DO UPDATE SET
          game_id = EXCLUDED.game_id,
          source = EXCLUDED.source,
          updated_at = EXCLUDED.updated_at
        """,
        market_id,
        game_id,
        _utcnow(),
    )


async def insert_game_state(
    conn: asyncpg.Connection,
    *,
    game_id: str,
    home_score: int,
    away_score: int,
    period: int | None,
    time_remaining: str | None,
    possession: str | None,
    home_team: str,
    away_team: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO game_state (
          game_id, ts, home_score, away_score, period, time_remaining, possession,
          home_team, away_team
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        """,
        game_id,
        _utcnow(),
        home_score,
        away_score,
        period,
        time_remaining,
        possession,
        home_team,
        away_team,
    )
