from __future__ import annotations

from datetime import datetime, timezone

from ploy_agent.sim.rules import directional_model_prob, should_enter
from ploy_agent.sim.types import SimProfile, SimSignal


def _signal(edge: float, conf: float = 0.7, model: float = 0.6) -> SimSignal:
    return SimSignal(
        ts=datetime.now(timezone.utc),
        market_id="m1",
        strategy_id="baseline_model",
        category="nba",
        question="Test?",
        model_prob=model if edge >= 0 else 1 - model,
        market_prob=0.5,
        edge_cents=edge,
        confidence=conf,
    )


def test_should_enter_buy_passes() -> None:
    p = SimProfile(id="t", min_edge_cents=3, min_confidence=0.55, min_model_prob=0.55)
    assert should_enter(_signal(5.0), p)


def test_should_enter_sell_uses_complement_prob() -> None:
    p = SimProfile(id="t", min_edge_cents=3, min_confidence=0.55, min_model_prob=0.55)
    s = SimSignal(
        ts=datetime.now(timezone.utc),
        market_id="m1",
        strategy_id="baseline_model",
        category="nba",
        question="Test?",
        model_prob=0.4,
        market_prob=0.5,
        edge_cents=-5.0,
        confidence=0.7,
    )
    assert directional_model_prob(s) == 0.6
    assert should_enter(s, p)


def test_should_enter_fails_low_edge() -> None:
    p = SimProfile(id="t", min_edge_cents=8, min_confidence=0.55, min_model_prob=0.55)
    assert not should_enter(_signal(5.0), p)
