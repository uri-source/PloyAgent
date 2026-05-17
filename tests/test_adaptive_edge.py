from __future__ import annotations

"""Tests for adaptive edge threshold logic (pure math, no DB)."""

# We test the multiplier logic by checking the formula directly
# since the actual function requires a DB connection.


def _compute_multiplier(hit_rate: float) -> float:
    """Mirror the multiplier logic from adaptive_edge.py."""
    _HOT_THRESHOLD = 0.60
    _COLD_THRESHOLD = 0.40
    _MIN_MULTIPLIER = 0.7
    _MAX_MULTIPLIER = 1.8

    if hit_rate >= _HOT_THRESHOLD:
        t = min((hit_rate - _HOT_THRESHOLD) / 0.20, 1.0)
        return 1.0 - t * (1.0 - _MIN_MULTIPLIER)
    elif hit_rate <= _COLD_THRESHOLD:
        t = min((_COLD_THRESHOLD - hit_rate) / 0.20, 1.0)
        return 1.0 + t * (_MAX_MULTIPLIER - 1.0)
    else:
        return 1.0


def test_neutral_no_change():
    assert _compute_multiplier(0.50) == 1.0


def test_hot_streak_tightens():
    m = _compute_multiplier(0.70)
    assert m < 1.0
    assert m >= 0.7


def test_cold_streak_widens():
    m = _compute_multiplier(0.30)
    assert m > 1.0
    assert m <= 1.8


def test_very_hot_hits_floor():
    m = _compute_multiplier(0.85)
    assert abs(m - 0.7) < 0.01


def test_very_cold_hits_ceiling():
    m = _compute_multiplier(0.15)
    assert abs(m - 1.8) < 0.01


def test_boundary_hot():
    """Exactly at hot threshold → multiplier = 1.0."""
    assert _compute_multiplier(0.60) == 1.0


def test_boundary_cold():
    """Exactly at cold threshold → multiplier = 1.0."""
    assert _compute_multiplier(0.40) == 1.0
