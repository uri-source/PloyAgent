from __future__ import annotations

"""Fair value time-decay.

Stale fair values should lose influence over time — the market has had time to absorb
information, so old signals are less meaningful. This module provides a decay multiplier
that reduces effective edge based on age of the fair value.

Decay model: exponential with configurable half-life.
  decay = 2^(-age_seconds / half_life_seconds)

Applied to edge_cents before composite scoring:
  effective_edge = edge_cents * decay_factor(age)
"""

import math
from datetime import datetime, timezone

# Half-life in seconds: edge loses 50% of signal strength after this duration
_HALF_LIFE_SEC = 180.0  # 3 minutes

# Minimum decay factor (floor) — never fully zero out
_MIN_DECAY = 0.1


def decay_factor(
    fair_value_ts: datetime,
    now: datetime | None = None,
    half_life_sec: float = _HALF_LIFE_SEC,
) -> float:
    """Return decay multiplier [_MIN_DECAY, 1.0] based on age of fair value."""
    if now is None:
        now = datetime.now(timezone.utc)

    if fair_value_ts.tzinfo is None:
        fair_value_ts = fair_value_ts.replace(tzinfo=timezone.utc)

    age_sec = max(0.0, (now - fair_value_ts).total_seconds())

    if age_sec <= 0:
        return 1.0

    decay = math.pow(2.0, -age_sec / half_life_sec)
    return max(_MIN_DECAY, decay)


def decayed_edge(
    edge_cents: float,
    fair_value_ts: datetime,
    now: datetime | None = None,
) -> float:
    """Return edge_cents multiplied by time-decay factor."""
    return edge_cents * decay_factor(fair_value_ts, now)
