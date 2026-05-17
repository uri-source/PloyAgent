from __future__ import annotations

from ploy_agent.common.kelly import kelly_fraction, kelly_display


def test_positive_edge_buy():
    """BUY signal with positive edge should produce positive Kelly."""
    kf = kelly_fraction(model_prob=0.65, market_price=0.50, edge_cents=15.0)
    assert kf > 0
    assert kf <= 0.15  # max cap


def test_no_edge_no_bet():
    """Zero or negative edge should return 0."""
    kf = kelly_fraction(model_prob=0.50, market_price=0.50, edge_cents=0.0)
    assert kf == 0.0


def test_sell_signal():
    """SELL signal (negative edge) should still produce positive Kelly for the short side."""
    kf = kelly_fraction(model_prob=0.35, market_price=0.50, edge_cents=-15.0)
    assert kf > 0


def test_extreme_price_returns_zero():
    """Prices at 0.01 or 0.99 should return 0 (degenerate odds)."""
    assert kelly_fraction(0.99, 0.99, 0.5) == 0.0
    assert kelly_fraction(0.01, 0.01, -0.5) == 0.0


def test_fractional_kelly_smaller_than_full():
    """Quarter-Kelly should be smaller than full Kelly."""
    full = kelly_fraction(0.70, 0.50, 20.0, fraction=1.0)
    quarter = kelly_fraction(0.70, 0.50, 20.0, fraction=0.25)
    assert quarter < full
    assert quarter > 0


def test_kelly_capped():
    """Even huge edge should be capped at max_kelly."""
    kf = kelly_fraction(0.95, 0.50, 45.0, max_kelly=0.10)
    assert kf <= 0.10


def test_kelly_display_no_bet():
    assert kelly_display(0.0) == "No bet"


def test_kelly_display_with_value():
    result = kelly_display(0.05, bankroll=1000.0)
    assert "5.0%" in result
    assert "$50" in result
