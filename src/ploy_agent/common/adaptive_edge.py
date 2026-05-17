from __future__ import annotations

"""Adaptive edge threshold.

Adjusts the effective MIN_EDGE_CENTS based on recent hit rate:
- Winning streak (hit rate > 60%) → tighten threshold (accept smaller edges)
- Losing streak (hit rate < 40%) → widen threshold (be more selective)
- Neutral → use configured default

This prevents over-trading during cold streaks and captures more value during hot streaks.
"""

import asyncpg

from ploy_agent.common.config import settings
from ploy_agent.common.logging_config import get_logger

log = get_logger("common.adaptive_edge")

# Lookback window
_LOOKBACK_DAYS = 3
_MIN_SAMPLES = 5

# Adjustment bounds
_MIN_MULTIPLIER = 0.7  # Can tighten to 70% of base (e.g. 3.0 → 2.1)
_MAX_MULTIPLIER = 1.8  # Can widen to 180% of base (e.g. 3.0 → 5.4)

# Hit rate thresholds
_HOT_THRESHOLD = 0.60
_COLD_THRESHOLD = 0.40


async def adaptive_min_edge(conn: asyncpg.Connection) -> float:
    """Return dynamically adjusted MIN_EDGE_CENTS based on recent performance."""
    base = settings.min_edge_cents

    row = await conn.fetchrow(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE pnl_cents > 0) AS wins
        FROM recommendations
        WHERE resolved_outcome IS NOT NULL
          AND resolved_at > NOW() - INTERVAL '3 days'
        """,
    )

    if not row or int(row["total"]) < _MIN_SAMPLES:
        return base

    total = int(row["total"])
    wins = int(row["wins"])
    hit_rate = wins / total

    if hit_rate >= _HOT_THRESHOLD:
        # Winning — tighten threshold to capture more edge
        # Linear interpolation: 60% → 1.0×, 80%+ → 0.7×
        t = min((hit_rate - _HOT_THRESHOLD) / 0.20, 1.0)
        multiplier = 1.0 - t * (1.0 - _MIN_MULTIPLIER)
    elif hit_rate <= _COLD_THRESHOLD:
        # Losing — widen threshold to be more selective
        # Linear interpolation: 40% → 1.0×, 20%- → 1.8×
        t = min((_COLD_THRESHOLD - hit_rate) / 0.20, 1.0)
        multiplier = 1.0 + t * (_MAX_MULTIPLIER - 1.0)
    else:
        multiplier = 1.0

    adjusted = base * multiplier

    if abs(multiplier - 1.0) > 0.05:
        log.info(
            "adaptive_edge_adjusted",
            base=base,
            adjusted=round(adjusted, 2),
            hit_rate=round(hit_rate, 2),
            total=total,
            wins=wins,
        )

    return adjusted
