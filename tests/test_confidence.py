"""Tests for common/confidence.py — statistical confidence scoring."""
from __future__ import annotations

from ploy_agent.common.confidence import statistical_confidence


def test_high_liquidity_boosts_confidence():
    """Deep book → higher confidence than shallow book."""
    conf_deep, _ = statistical_confidence(depth_1c=100_000, edge_cents=5.0, mid=0.5)
    conf_shallow, _ = statistical_confidence(depth_1c=100, edge_cents=5.0, mid=0.5)
    assert conf_deep > conf_shallow


def test_tight_spread_boosts_confidence():
    """Tight spread → higher confidence."""
    conf_tight, _ = statistical_confidence(depth_1c=10_000, spread=0.01, edge_cents=5.0, mid=0.5)
    conf_wide, _ = statistical_confidence(depth_1c=10_000, spread=0.10, edge_cents=5.0, mid=0.5)
    assert conf_tight > conf_wide


def test_binary_pair_boost():
    """Binary pair with deviation should boost confidence."""
    conf_pair, _ = statistical_confidence(
        depth_1c=10_000, n_siblings=1, sum_deviation=0.08,
        edge_cents=5.0, mid=0.5, is_binary_pair=True,
    )
    conf_no_sib, _ = statistical_confidence(
        depth_1c=10_000, edge_cents=5.0, mid=0.5,
    )
    assert conf_pair > conf_no_sib


def test_confidence_clamped():
    """Confidence should always be in [0.15, 0.95]."""
    conf_max, _ = statistical_confidence(
        depth_1c=1_000_000, spread=0.001, n_siblings=5,
        sum_deviation=0.2, edge_cents=50.0, mid=0.5, is_binary_pair=True,
    )
    conf_min, _ = statistical_confidence(
        depth_1c=0, edge_cents=0.1, mid=0.99,
    )
    assert 0.15 <= conf_min <= 0.95
    assert 0.15 <= conf_max <= 0.95


def test_extreme_mid_reduces_confidence():
    """Markets near 0 or 1 should have lower confidence."""
    conf_center, _ = statistical_confidence(depth_1c=10_000, edge_cents=5.0, mid=0.5)
    conf_extreme, _ = statistical_confidence(depth_1c=10_000, edge_cents=5.0, mid=0.98)
    assert conf_center > conf_extreme


def test_returns_reasoning_string():
    """Should return a non-empty reasoning string."""
    _, reasoning = statistical_confidence(depth_1c=10_000, edge_cents=5.0, mid=0.5)
    assert isinstance(reasoning, str)
    assert len(reasoning) > 0
