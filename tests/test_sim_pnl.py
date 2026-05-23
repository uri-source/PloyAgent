from __future__ import annotations

from ploy_agent.common.pnl import compute_pnl_cents, outcome_from_final_mid, trade_direction


def test_buy_win() -> None:
    assert compute_pnl_cents(0.5, "buy", 1) == 50.0


def test_buy_lose() -> None:
    assert compute_pnl_cents(0.5, "buy", 0) == -50.0


def test_sell_win() -> None:
    assert compute_pnl_cents(0.6, "sell", 0) == 60.0


def test_trade_direction() -> None:
    assert trade_direction(3.0) == "buy"
    assert trade_direction(-2.0) == "sell"


def test_outcome_from_mid() -> None:
    assert outcome_from_final_mid(0.95) == 1
    assert outcome_from_final_mid(0.05) == 0
    assert outcome_from_final_mid(0.5) is None
