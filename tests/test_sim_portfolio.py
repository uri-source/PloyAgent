from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ploy_agent.common.config import settings
from ploy_agent.sim.portfolio import ProfilePortfolio
from ploy_agent.sim.types import SimProfile, SimSignal


def _sig(
    edge: float,
    ts: datetime | None = None,
    *,
    end_date: datetime | None = None,
) -> SimSignal:
    return SimSignal(
        ts=ts or datetime.now(timezone.utc),
        market_id="m1",
        strategy_id="s1",
        category="nba",
        question="Q",
        model_prob=0.6,
        market_prob=0.5,
        edge_cents=edge,
        confidence=0.7,
        score=1.0,
        end_date=end_date,
    )


def test_enter_and_close_on_resolution() -> None:
    p = SimProfile(id="p1", min_edge_cents=3, min_confidence=0.5, min_model_prob=0.5)
    port = ProfilePortfolio(p)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    port.try_enter(_sig(5.0, t0))
    closed = port.process_signal(_sig(5.0, t0 + timedelta(seconds=1)), market_resolved=True, resolved_outcome=1)
    assert len(closed) == 1
    assert closed[0].close_reason == "resolution"
    assert closed[0].pnl_cents == 50.0


def test_try_enter_rejects_far_end_date_when_horizon_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sim_max_hours_to_resolution", 48.0)
    p = SimProfile(id="p1", min_edge_cents=3, min_confidence=0.5, min_model_prob=0.5)
    port = ProfilePortfolio(p)
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    far_end = t0 + timedelta(days=7)
    assert port.try_enter(_sig(5.0, t0, end_date=far_end)) is None


def test_try_enter_allows_near_end_date_when_horizon_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sim_max_hours_to_resolution", 48.0)
    p = SimProfile(id="p1", min_edge_cents=3, min_confidence=0.5, min_model_prob=0.5)
    port = ProfilePortfolio(p)
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    near_end = t0 + timedelta(hours=24)
    assert port.try_enter(_sig(5.0, t0, end_date=near_end)) is not None


def test_try_enter_rejects_missing_end_date_when_horizon_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sim_max_hours_to_resolution", 48.0)
    p = SimProfile(id="p1", min_edge_cents=3, min_confidence=0.5, min_model_prob=0.5)
    port = ProfilePortfolio(p)
    assert port.try_enter(_sig(5.0)) is None


def test_try_enter_rejects_non_persistent_edge() -> None:
    p = SimProfile(id="p1", min_edge_cents=3, min_confidence=0.5, min_model_prob=0.5)
    port = ProfilePortfolio(p)
    base = _sig(5.0)
    sig = SimSignal(
        ts=base.ts,
        market_id=base.market_id,
        strategy_id=base.strategy_id,
        category=base.category,
        question=base.question,
        model_prob=base.model_prob,
        market_prob=base.market_prob,
        edge_cents=base.edge_cents,
        confidence=base.confidence,
        score=base.score,
        end_date=base.end_date,
        edge_persistent=False,
    )
    assert port.try_enter(sig) is None


def test_try_enter_ignores_horizon_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "sim_max_hours_to_resolution", 0.0)
    p = SimProfile(id="p1", min_edge_cents=3, min_confidence=0.5, min_model_prob=0.5)
    port = ProfilePortfolio(p)
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    far_end = t0 + timedelta(days=30)
    assert port.try_enter(_sig(5.0, t0, end_date=far_end)) is not None
