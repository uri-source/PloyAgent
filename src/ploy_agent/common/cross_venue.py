from __future__ import annotations

"""Fee-adjusted cross-venue (Polymarket vs Kalshi) fair value and edge math."""


def normalize_kalshi_yes_prob(kalshi_mid: float, outcome_map: str) -> float:
    """Map Kalshi mid to comparable Polymarket YES probability."""
    k = float(kalshi_mid)
    if outcome_map == "inverted":
        k = 1.0 - k
    return max(0.01, min(0.99, k))


def implied_yes_fair_from_kalshi(
    kalshi_mid: float,
    *,
    outcome_map: str,
    kalshi_fee_rate: float,
) -> float:
    """Conservative fair YES on Polymarket implied by Kalshi (after fee haircut)."""
    yes = normalize_kalshi_yes_prob(kalshi_mid, outcome_map)
    fee = max(0.0, float(kalshi_fee_rate))
    return max(0.01, min(0.99, yes * (1.0 - fee)))


def edge_cents_from_venues(
    poly_mid: float,
    kalshi_mid: float,
    *,
    outcome_map: str,
    poly_fee_rate: float,
    kalshi_fee_rate: float,
) -> tuple[float, float]:
    """
    Returns (model_prob, edge_cents) for trading the Polymarket leg.
    model_prob = fee-adjusted fair from Kalshi; market_prob = poly_mid.
    """
    model_prob = implied_yes_fair_from_kalshi(
        kalshi_mid, outcome_map=outcome_map, kalshi_fee_rate=kalshi_fee_rate
    )
    poly = max(0.01, min(0.99, float(poly_mid)))
    # model_prob already includes Kalshi fee haircut; only adjust Polymarket leg
    poly_effective = poly * (1.0 + max(0.0, poly_fee_rate))
    edge = (model_prob - poly_effective) * 100.0
    return model_prob, edge


def spread_cents(poly_mid: float, kalshi_mid: float, *, outcome_map: str) -> float:
    """Raw mid gap in cents (before fees)."""
    k = normalize_kalshi_yes_prob(kalshi_mid, outcome_map)
    return (k - float(poly_mid)) * 100.0


def spread_widening(recent_spreads: list[float], *, min_samples: int = 3) -> bool:
    """True if spread magnitude grew from oldest to newest sample."""
    if len(recent_spreads) < min_samples:
        return False
    first = abs(recent_spreads[0])
    last = abs(recent_spreads[-1])
    return last > first + 0.5
