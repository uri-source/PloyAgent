from __future__ import annotations

"""Shared hypothetical P&L for binary Polymarket YES positions (cents per $1 share)."""


def trade_direction(edge_cents: float) -> str:
    return "buy" if edge_cents > 0 else "sell"


def compute_pnl_cents(
    entry_price: float,
    direction: str,
    outcome: int,
) -> float:
    """Outcome: 1 = YES wins, 0 = NO wins. Entry at market mid (0-1)."""
    is_buy = direction == "buy"
    if is_buy:
        return ((1.0 - entry_price) * 100.0) if outcome == 1 else (-entry_price * 100.0)
    return (entry_price * 100.0) if outcome == 0 else (-(1.0 - entry_price) * 100.0)


def outcome_from_final_mid(mid: float) -> int | None:
    if mid > 0.9:
        return 1
    if mid < 0.1:
        return 0
    return None
