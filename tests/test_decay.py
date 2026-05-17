from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ploy_agent.common.fair_value_decay import decay_factor, decayed_edge


def test_fresh_signal_no_decay():
    """Signal from now should have decay = 1.0."""
    now = datetime.now(timezone.utc)
    assert decay_factor(now, now) == 1.0


def test_half_life_decay():
    """Signal at exactly half-life should decay to ~0.5."""
    now = datetime.now(timezone.utc)
    ts = now - timedelta(seconds=180)  # default half-life = 180s
    d = decay_factor(ts, now)
    assert 0.49 < d < 0.51


def test_very_old_signal_floors():
    """Signal from long ago should floor at _MIN_DECAY (0.1)."""
    now = datetime.now(timezone.utc)
    ts = now - timedelta(hours=1)
    d = decay_factor(ts, now)
    assert d == 0.1


def test_decayed_edge_reduces():
    """Decayed edge should be smaller than raw edge for stale signals."""
    now = datetime.now(timezone.utc)
    ts = now - timedelta(seconds=300)
    raw_edge = 10.0
    de = decayed_edge(raw_edge, ts, now)
    assert 0 < de < raw_edge


def test_fresh_edge_unchanged():
    """Fresh signal should not reduce edge."""
    now = datetime.now(timezone.utc)
    raw_edge = 5.0
    de = decayed_edge(raw_edge, now, now)
    assert de == raw_edge


def test_naive_timestamp_handled():
    """Naive datetime (no tzinfo) should be treated as UTC."""
    now = datetime.now(timezone.utc)
    ts = datetime(2020, 1, 1, 0, 0, 0)  # naive, very old
    d = decay_factor(ts, now)
    assert d == 0.1  # floored
