from __future__ import annotations

import pytest

from ploy_agent.common.cross_venue import (
    edge_cents_from_venues,
    implied_yes_fair_from_kalshi,
    normalize_kalshi_yes_prob,
    spread_widening,
)


def test_normalize_inverted():
    assert normalize_kalshi_yes_prob(0.7, "inverted") == pytest.approx(0.3)


def test_implied_yes_fair_applies_kalshi_fee():
    fair = implied_yes_fair_from_kalshi(0.5, outcome_map="same", kalshi_fee_rate=0.02)
    assert fair == 0.49


def test_edge_positive_when_poly_expensive_vs_kalshi():
    model, edge = edge_cents_from_venues(
        0.55,
        0.45,
        outcome_map="same",
        poly_fee_rate=0.02,
        kalshi_fee_rate=0.01,
    )
    assert model < 0.45
    assert edge < 0


def test_edge_buy_when_poly_cheap():
    _, edge = edge_cents_from_venues(
        0.40,
        0.50,
        outcome_map="same",
        poly_fee_rate=0.02,
        kalshi_fee_rate=0.01,
    )
    assert edge > 0


def test_spread_widening_detects_growth():
    assert spread_widening([1.0, 2.0, 4.0, 6.0]) is True
    assert spread_widening([6.0, 4.0, 2.0]) is False
