from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg

from ploy_agent.common.db import get_pool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_market(
    conn: asyncpg.Connection,
    *,
    market_id: str,
    slug: str | None,
    question: str | None,
    resolution_criteria: str | None,
    end_date: datetime | None,
    category: str | None,
    status: str | None,
    condition_id: str | None,
    clob_asset_id: str,
    companion_clob_asset_id: str | None,
    gamma_event_id: str | None = None,
    event_slug: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO markets (
          id, slug, question, resolution_criteria, end_date, category, status,
          condition_id, clob_asset_id, companion_clob_asset_id,
          gamma_event_id, event_slug, updated_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (id) DO UPDATE SET
          slug = EXCLUDED.slug,
          question = EXCLUDED.question,
          resolution_criteria = EXCLUDED.resolution_criteria,
          end_date = EXCLUDED.end_date,
          category = EXCLUDED.category,
          status = EXCLUDED.status,
          condition_id = EXCLUDED.condition_id,
          clob_asset_id = EXCLUDED.clob_asset_id,
          companion_clob_asset_id = EXCLUDED.companion_clob_asset_id,
          gamma_event_id = COALESCE(EXCLUDED.gamma_event_id, markets.gamma_event_id),
          event_slug = COALESCE(EXCLUDED.event_slug, markets.event_slug),
          updated_at = EXCLUDED.updated_at
        """,
        market_id,
        slug,
        question,
        resolution_criteria,
        end_date,
        category,
        status,
        condition_id,
        clob_asset_id,
        companion_clob_asset_id,
        gamma_event_id,
        event_slug,
        _utcnow(),
    )


async def insert_price_row(
    conn: asyncpg.Connection,
    *,
    market_id: str,
    bid: float | None,
    ask: float | None,
    mid: float | None,
    depth_1c: float | None,
    volume_24h: float | None,
    snapshot_kind: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO prices (market_id, ts, bid, ask, mid, depth_1c, volume_24h, snapshot_kind)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        """,
        market_id,
        _utcnow(),
        bid,
        ask,
        mid,
        depth_1c,
        volume_24h,
        snapshot_kind,
    )


async def insert_order_book_snapshot(
    conn: asyncpg.Connection,
    *,
    market_id: str,
    bids: list[dict[str, Any]],
    asks: list[dict[str, Any]],
    trigger_reason: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO order_book_snapshots (market_id, ts, bids_json, asks_json, trigger_reason)
        VALUES ($1,$2,$3::jsonb,$4::jsonb,$5)
        """,
        market_id,
        _utcnow(),
        json.dumps(bids),
        json.dumps(asks),
        trigger_reason,
    )


async def load_markets_for_assets(conn: asyncpg.Connection) -> dict[str, str]:
    """Map clob_asset_id -> market_id."""
    rows = await conn.fetch(
        "SELECT id, clob_asset_id, companion_clob_asset_id FROM markets WHERE status IS DISTINCT FROM 'closed'"
    )
    m: dict[str, str] = {}
    for r in rows:
        m[str(r["clob_asset_id"])] = str(r["id"])
        if r["companion_clob_asset_id"]:
            m[str(r["companion_clob_asset_id"])] = str(r["id"])
    return m
