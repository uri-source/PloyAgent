from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
