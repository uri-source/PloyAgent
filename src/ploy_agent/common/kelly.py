from __future__ import annotations

"""Kelly criterion bet sizing (fractional, display-only).

Kelly fraction = (bp - q) / b
where:
  p = model probability of winning
  q = 1 - p
  b = net odds (payout ratio minus 1)

For Polymarket:
  BUY YES at price `c` → b = (1 - c) / c  (pay c, win 1)
  SELL YES at price `c` → b = c / (1 - c)  (pay 1-c, win 1)

We apply a fractional Kelly (default 0.25) to reduce variance.
"""


def kelly_fraction(
    model_prob: float,
    market_price: float,
    edge_cents: float,
    *,
    fraction: float = 0.25,
    max_kelly: float = 0.15,
) -> float:
    """Return suggested fraction of bankroll to wager (0.0 to max_kelly).

    Returns 0.0 if the edge is non-positive or inputs are degenerate.
    """
    if market_price <= 0.01 or market_price >= 0.99:
        return 0.0

    is_buy = edge_cents > 0

    if is_buy:
        # Betting on YES at cost = market_price
        p = model_prob
        b = (1.0 - market_price) / market_price
    else:
        # Betting on NO at cost = 1 - market_price
        p = 1.0 - model_prob
        b = market_price / (1.0 - market_price)

    if b <= 0:
        return 0.0

    q = 1.0 - p
    kelly = (b * p - q) / b

    if kelly <= 0:
        return 0.0

    # Apply fractional Kelly and cap
    sized = kelly * fraction
    return min(sized, max_kelly)


def kelly_display(kelly_frac: float, bankroll: float = 1000.0) -> str:
    """Format kelly as a human-readable suggestion."""
    if kelly_frac <= 0:
        return "No bet"
    dollars = kelly_frac * bankroll
    pct = kelly_frac * 100
    return f"{pct:.1f}% (${dollars:.0f} of ${bankroll:.0f})"
