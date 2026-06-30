from __future__ import annotations

import asyncpg

from ploy_agent.common.logging_config import get_logger

log = get_logger("strategies.auto_disable")

# Auto-disable strategies with negative ROI on recommendations or sim trades.

_MIN_RESOLVED = 10
_LOOKBACK_DAYS = 7


async def _negative_roi_from_recommendations(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT strategy_id,
               COUNT(*) AS n,
               SUM(pnl_cents) AS total_pnl
        FROM recommendations
        WHERE resolved_outcome IS NOT NULL
          AND resolved_at > NOW() - ($1::text || ' days')::interval
          AND strategy_id IS NOT NULL
        GROUP BY strategy_id
        HAVING COUNT(*) >= $2
        """,
        str(_LOOKBACK_DAYS),
        _MIN_RESOLVED,
    )
    disabled: set[str] = set()
    for r in rows:
        total_pnl = float(r["total_pnl"] or 0)
        if total_pnl < 0:
            disabled.add(str(r["strategy_id"]))
            log.info(
                "strategy_auto_disabled_recs",
                strategy_id=r["strategy_id"],
                resolved=r["n"],
                total_pnl=round(total_pnl, 1),
            )
    return disabled


async def _negative_roi_from_sim(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT strategy_id,
               COUNT(*) AS n,
               SUM(pnl_cents) AS total_pnl
        FROM sim_trades
        WHERE status = 'closed'
          AND pnl_cents IS NOT NULL
          AND closed_at > NOW() - ($1::text || ' days')::interval
          AND strategy_id IS NOT NULL
        GROUP BY strategy_id
        HAVING COUNT(*) >= $2
        """,
        str(_LOOKBACK_DAYS),
        _MIN_RESOLVED,
    )
    disabled: set[str] = set()
    for r in rows:
        total_pnl = float(r["total_pnl"] or 0)
        if total_pnl < 0:
            disabled.add(str(r["strategy_id"]))
            log.info(
                "strategy_auto_disabled_sim",
                strategy_id=r["strategy_id"],
                closed=r["n"],
                total_pnl=round(total_pnl, 1),
            )
    return disabled


async def disabled_strategy_ids(conn: asyncpg.Connection) -> set[str]:
    """Return strategy_ids disabled due to negative ROI on recs or sim trades."""
    recs = await _negative_roi_from_recommendations(conn)
    sim = await _negative_roi_from_sim(conn)
    return recs | sim
