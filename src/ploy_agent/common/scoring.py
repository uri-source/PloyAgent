from __future__ import annotations

import math
from datetime import datetime, timezone


def edge_cents(model_prob: float, market_mid: float) -> float:
    return (model_prob - market_mid) * 100.0


def time_factor_hours(hours_to_resolution: float) -> float:
    return 1.0 / (1.0 + max(hours_to_resolution, 0.0))


def risk_reward_factor(market_mid: float, edge_cents_val: float) -> float:
    """Penalize trades with lopsided risk/reward (entry far from 0.50).

    At mid=0.50: factor=1.0 (symmetric payoff).
    At mid=0.85 buying: win=15¢, loss=85¢ → ratio=0.18 → factor≈0.42.
    Clamp to [0.15, 1.0] so extreme entries are suppressed but not zeroed.
    """
    mid = max(min(market_mid, 0.99), 0.01)
    is_buy = edge_cents_val > 0
    if is_buy:
        win_payout = (1.0 - mid) * 100
        loss_payout = mid * 100
    else:
        win_payout = mid * 100
        loss_payout = (1.0 - mid) * 100
    ratio = win_payout / loss_payout if loss_payout > 0 else 1.0
    return max(min(ratio ** 0.5, 1.0), 0.15)


def composite_score(
    edge_cents_val: float,
    depth_1c: float,
    confidence: float,
    hours_to_resolution: float,
    market_mid: float = 0.5,
) -> float:
    risk_rw = risk_reward_factor(market_mid, edge_cents_val)
    return (
        abs(edge_cents_val)
        * math.log1p(max(depth_1c, 0.0))
        * confidence
        * time_factor_hours(hours_to_resolution)
        * risk_rw
    )


def passes_entry_price_gate(
    market_prob: float,
    price_min: float = 0.35,
    price_max: float = 0.65,
) -> bool:
    """Return True if market_prob is within the acceptable entry price range."""
    return price_min <= market_prob <= price_max


def passes_risk_reward_gate(
    market_mid: float,
    edge_cents_val: float,
    min_rr: float = 0.30,
) -> bool:
    """Return True if risk_reward_factor meets the minimum threshold."""
    return risk_reward_factor(market_mid, edge_cents_val) >= min_rr


def hours_until(end: datetime | None, now: datetime | None = None) -> float:
    if end is None:
        return 24.0
    if now is None:
        now = datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max((end - now).total_seconds() / 3600.0, 0.0)
