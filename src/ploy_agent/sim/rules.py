from __future__ import annotations

from ploy_agent.common.pnl import compute_pnl_cents, trade_direction
from ploy_agent.sim.types import SimProfile, SimSignal

__all__ = [
    "SimProfile",
    "SimSignal",
    "should_enter",
    "directional_model_prob",
    "compute_pnl_cents",
    "trade_direction",
]


def directional_model_prob(signal: SimSignal) -> float:
    if signal.edge_cents >= 0:
        return signal.model_prob
    return 1.0 - signal.model_prob


def should_enter(signal: SimSignal, profile: SimProfile) -> bool:
    if profile.strategy_ids and signal.strategy_id not in profile.strategy_ids:
        return False
    edge = signal.edge_cents
    if abs(edge) < profile.min_edge_cents:
        return False
    if signal.confidence < profile.min_confidence:
        return False
    if directional_model_prob(signal) < profile.min_model_prob:
        return False
    return True


def is_reverse_signal(signal: SimSignal, position_direction: str) -> bool:
    sig_dir = trade_direction(signal.edge_cents)
    return sig_dir != position_direction
