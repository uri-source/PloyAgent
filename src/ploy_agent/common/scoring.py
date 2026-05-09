from __future__ import annotations

import math
from datetime import datetime, timezone


def edge_cents(model_prob: float, market_mid: float) -> float:
    return (model_prob - market_mid) * 100.0


def time_factor_hours(hours_to_resolution: float) -> float:
    return 1.0 / (1.0 + max(hours_to_resolution, 0.0))


def composite_score(
    edge_cents_val: float,
    depth_1c: float,
    confidence: float,
    hours_to_resolution: float,
) -> float:
    return edge_cents_val * math.log1p(max(depth_1c, 0.0)) * confidence * time_factor_hours(
        hours_to_resolution
    )


def hours_until(end: datetime | None, now: datetime | None = None) -> float:
    if end is None:
        return 24.0
    if now is None:
        now = datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max((end - now).total_seconds() / 3600.0, 0.0)
