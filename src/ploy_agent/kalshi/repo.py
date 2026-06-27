from __future__ import annotations

from datetime import datetime

import asyncpg


async def upsert_kalshi_market(
    conn: asyncpg.Connection,
    *,
    ticker: str,
    title: str | None,
    status: str | None,
    series_ticker: str | None,
    close_time: datetime | None,
) -> None:
    await conn.execute(
        """
        INSERT INTO kalshi_markets (ticker, title, status, series_ticker, close_time, updated_at)
        VALUES ($1, $2, $3, $4, $5, NOW())
        ON CONFLICT (ticker) DO UPDATE SET
          title = EXCLUDED.title,
          status = EXCLUDED.status,
          series_ticker = EXCLUDED.series_ticker,
          close_time = EXCLUDED.close_time,
          updated_at = NOW()
        """,
        ticker,
        title,
        status,
        series_ticker,
        close_time,
    )


async def insert_kalshi_price(
    conn: asyncpg.Connection,
    *,
    ticker: str,
    ts: datetime,
    bid: float | None,
    ask: float | None,
    mid: float | None,
    depth_1c: float,
) -> None:
    await conn.execute(
        """
        INSERT INTO kalshi_prices (ticker, ts, bid, ask, mid, depth_1c, snapshot_kind)
        VALUES ($1, $2, $3, $4, $5, $6, 'poll')
        """,
        ticker,
        ts,
        bid,
        ask,
        mid,
        depth_1c,
    )


async def latest_kalshi_price(conn: asyncpg.Connection, ticker: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT ticker, ts, bid, ask, mid, depth_1c
        FROM kalshi_prices
        WHERE ticker = $1 AND mid IS NOT NULL
        ORDER BY ts DESC
        LIMIT 1
        """,
        ticker,
    )


async def active_pairs(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, label, poly_market_id, kalshi_ticker, outcome_map,
               resolution_aligned, notes, active
        FROM cross_venue_pairs
        WHERE active = TRUE
        ORDER BY id
        """
    )


async def pair_for_poly_market(
    conn: asyncpg.Connection, poly_market_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """
        SELECT id, label, poly_market_id, kalshi_ticker, outcome_map,
               resolution_aligned, notes, active
        FROM cross_venue_pairs
        WHERE poly_market_id = $1 AND active = TRUE
        LIMIT 1
        """,
        poly_market_id,
    )


async def upsert_pair(
    conn: asyncpg.Connection,
    *,
    pair_id: str,
    label: str,
    poly_market_id: str,
    kalshi_ticker: str,
    outcome_map: str,
    resolution_aligned: bool,
    notes: str | None,
    active: bool,
) -> None:
    await conn.execute(
        """
        INSERT INTO cross_venue_pairs (
          id, label, poly_market_id, kalshi_ticker, outcome_map,
          resolution_aligned, notes, active, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
        ON CONFLICT (id) DO UPDATE SET
          label = EXCLUDED.label,
          poly_market_id = EXCLUDED.poly_market_id,
          kalshi_ticker = EXCLUDED.kalshi_ticker,
          outcome_map = EXCLUDED.outcome_map,
          resolution_aligned = EXCLUDED.resolution_aligned,
          notes = EXCLUDED.notes,
          active = EXCLUDED.active,
          updated_at = NOW()
        """,
        pair_id,
        label,
        poly_market_id,
        kalshi_ticker,
        outcome_map,
        resolution_aligned,
        notes,
        active,
    )


async def recent_spread_samples(
    conn: asyncpg.Connection,
    *,
    poly_market_id: str,
    kalshi_ticker: str,
    outcome_map: str,
    window_minutes: int = 5,
) -> list[float]:
    """Mid spread (kalshi - poly) in cents over the window."""
    from ploy_agent.common.cross_venue import normalize_kalshi_yes_prob

    rows = await conn.fetch(
        """
        SELECT p.mid AS poly_mid, k.mid AS kalshi_mid, p.ts
        FROM prices p
        JOIN LATERAL (
          SELECT mid, ts FROM kalshi_prices
          WHERE ticker = $2 AND mid IS NOT NULL AND ts <= p.ts + INTERVAL '30 seconds'
          ORDER BY ts DESC LIMIT 1
        ) k ON true
        WHERE p.market_id = $1 AND p.mid IS NOT NULL
          AND p.ts > NOW() - ($3::text || ' minutes')::interval
        ORDER BY p.ts ASC
        LIMIT 120
        """,
        poly_market_id,
        kalshi_ticker,
        str(window_minutes),
    )
    out: list[float] = []
    for r in rows:
        k_yes = normalize_kalshi_yes_prob(float(r["kalshi_mid"]), outcome_map)
        out.append((k_yes - float(r["poly_mid"])) * 100.0)
    return out
