from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ploy_agent.sim.portfolio import ProfilePortfolio
from ploy_agent.sim.types import SimProfile, SimSignal


def _sig(edge: float, ts: datetime | None = None) -> SimSignal:
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
