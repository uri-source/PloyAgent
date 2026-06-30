from __future__ import annotations

from ploy_agent.strategies.book_imbalance import (
    _IMBALANCE_THRESHOLD,
    _BUY_IMBALANCE_MULTIPLIER,
    _sustained_same_sign,
    _snapshot_imbalance,
)


def test_sustained_same_sign_requires_two_of_three():
    assert _sustained_same_sign([0.4, 0.5, -0.2]) is True
    assert _sustained_same_sign([0.4, -0.5, -0.2]) is True
    assert _sustained_same_sign([0.4, -0.5]) is False


def test_buy_threshold_is_doubled():
    assert _IMBALANCE_THRESHOLD * _BUY_IMBALANCE_MULTIPLIER == 0.70


def test_snapshot_imbalance_insufficient_depth():
    bids = [{"size": 100}]
    asks = [{"size": 100}]
    assert _snapshot_imbalance(bids, asks) is None
