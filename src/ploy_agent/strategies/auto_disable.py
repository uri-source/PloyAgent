from __future__ import annotations

"""Strategy auto-disable: track per-strategy ROI and skip losers.

Queries recently resolved recommendations. If a strategy has >= MIN_RESOLVED picks and
cumulative P&L is negative, it's marked as disabled until performance recovers.
"""

import asyncpg

from ploy_agent.common.logging_config import get_logger

log = get_logger("strategies.auto_disable")

# Minimum resolved picks before we judge a strategy
_MIN_RESOLVED = 10
# Lookback window for performance evaluation
_LOOKBACK_DAYS = 7


async def disabled_strategy_ids(conn: asyncpg.Connection) -> set[str]:
    """Return set of strategy_ids that should be auto-disabled due to negative ROI."""
    rows = await conn.fetch(
        """
        SELECT strategy_id,
               COUNT(*) AS n,
               SUM(pnl_cents) AS total_pnl
        FROM recommendations
        WHERE resolved_outcome IS NOT NULL
          AND resolved_at > NOW() - INTERVAL '7 days'
          AND strategy_id IS NOT NULL
        GROUP BY strategy_id
        HAVING COUNT(*) >= $1
        """,
        _MIN_RESOLVED,
    )
    disabled: set[str] = set()
    for r in rows:
        total_pnl = float(r["total_pnl"] or 0)
        if total_pnl < 0:
            disabled.add(str(r["strategy_id"]))
            log.info(
                "strategy_auto_disabled",
                strategy_id=r["strategy_id"],
                resolved=r["n"],
                total_pnl=round(total_pnl, 1),
            )
    return disabled
