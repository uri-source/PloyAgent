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
    category: str | None = None,
    market_type: str | None = None,
    question: str | None = None,
    auto_approve: bool = False,
) -> int:
    status = "approved" if auto_approve else "pending"
    edge_cents = float(payload.get("edge_cents", 0))
    edge_direction = "buy" if edge_cents > 0 else "sell"
    row = await conn.fetchrow(
        """
        INSERT INTO recommendations
          (market_id, ts, score, status, payload_json, strategy_id,
           category, market_type, question, edge_direction)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10)
        RETURNING id
        """,
        market_id,
        _utcnow(),
        score,
        status,
        json.dumps(payload),
        strategy_id,
        category,
        market_type,
        question[:500] if question else None,
        edge_direction,
    )
    assert row is not None
    rec_id = int(row["id"])

    # If auto-approved, use the market_prob from the signal as entry price.
    # This is the mid at signal generation time — NOT the latest price,
    # which may have moved significantly if the market already resolved.
    if auto_approve:
        entry = float(payload.get("market_prob", 0.5))
        await conn.execute(
            "UPDATE recommendations SET entry_price = $2 WHERE id = $1",
            rec_id,
            entry,
        )

    return rec_id


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
    # On approval, snapshot the current market mid as the real entry price
    if status == "approved":
        await conn.execute(
            """
            UPDATE recommendations
            SET status = $2,
                human_notes = COALESCE($3, human_notes),
                entry_price = COALESCE(
                    (SELECT mid FROM prices WHERE market_id = recommendations.market_id
                     AND mid IS NOT NULL ORDER BY ts DESC LIMIT 1),
                    entry_price
                )
            WHERE id = $1
            """,
            rec_id,
            status,
            notes,
        )
    else:
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
    # Check for ANY unresolved recommendation for these markets — not just
    # within the time window.  This prevents duplicate rows for the same
    # position when a market stays in the top-N across multiple notifier ticks.
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (market_id, COALESCE(strategy_id, ''))
          id, market_id, strategy_id, status, slack_channel, slack_ts
        FROM recommendations
        WHERE market_id = ANY($1::text[])
          AND resolved_outcome IS NULL
        ORDER BY market_id, COALESCE(strategy_id, ''), ts DESC
        """,
        market_ids,
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
