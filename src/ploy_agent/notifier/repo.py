from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class RecommendationRef:
    rec_id: int
    market_id: str
    strategy_id: str | None
    status: str
    slack_channel: str | None
    slack_ts: str | None


async def insert_recommendation(
    conn: asyncpg.Connection,
    *,
    market_id: str,
    score: float,
    payload: dict[str, Any],
    strategy_id: str | None = None,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO recommendations (market_id, ts, score, status, payload_json, strategy_id)
        VALUES ($1,$2,$3,'pending',$4::jsonb,$5)
        RETURNING id
        """,
        market_id,
        _utcnow(),
        score,
        json.dumps(payload),
        strategy_id,
    )
    assert row is not None
    return int(row["id"])


async def update_slack_refs(conn: asyncpg.Connection, rec_id: int, channel: str, ts: str) -> None:
    await conn.execute(
        "UPDATE recommendations SET slack_channel = $2, slack_ts = $3 WHERE id = $1",
        rec_id,
        channel,
        ts,
    )


async def update_telegram_refs(
    conn: asyncpg.Connection, rec_id: int, chat_id: str, message_id: int
) -> None:
    await conn.execute(
        "UPDATE recommendations SET telegram_chat_id = $2, telegram_message_id = $3 WHERE id = $1",
        rec_id,
        chat_id,
        message_id,
    )


async def set_status(conn: asyncpg.Connection, rec_id: int, status: str, notes: str | None) -> None:
    await conn.execute(
        """
        UPDATE recommendations
        SET status = $2, human_notes = COALESCE($3, human_notes)
        WHERE id = $1
        """,
        rec_id,
        status,
        notes,
    )


async def latest_slack_message_ref(
    conn: asyncpg.Connection, channel: str
) -> tuple[str, str] | None:
    row = await conn.fetchrow(
        """
        SELECT slack_channel, slack_ts
        FROM recommendations
        WHERE slack_channel = $1
          AND slack_ts IS NOT NULL
          AND slack_ts <> ''
        ORDER BY ts DESC
        LIMIT 1
        """,
        channel,
    )
    if not row:
        return None
    return str(row["slack_channel"]), str(row["slack_ts"])


async def recent_recommendation_refs(
    conn: asyncpg.Connection,
    market_ids: list[str],
    *,
    window_minutes: int = 15,
) -> dict[tuple[str, str | None], RecommendationRef]:
    if not market_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (market_id, COALESCE(strategy_id, ''))
          id, market_id, strategy_id, status, slack_channel, slack_ts
        FROM recommendations
        WHERE market_id = ANY($1::text[])
          AND ts > NOW() - ($2::text || ' minutes')::interval
        ORDER BY market_id, COALESCE(strategy_id, ''), ts DESC
        """,
        market_ids,
        str(window_minutes),
    )
    out: dict[tuple[str, str | None], RecommendationRef] = {}
    for row in rows:
        key = (str(row["market_id"]), str(row["strategy_id"]) if row["strategy_id"] else None)
        out[key] = RecommendationRef(
            rec_id=int(row["id"]),
            market_id=str(row["market_id"]),
            strategy_id=str(row["strategy_id"]) if row["strategy_id"] else None,
            status=str(row["status"]),
            slack_channel=str(row["slack_channel"]) if row["slack_channel"] else None,
            slack_ts=str(row["slack_ts"]) if row["slack_ts"] else None,
        )
    return out
