from __future__ import annotations

from ploy_agent.sim.types import SimProfile


def profile_id(min_edge: float, min_conf: float, min_model: float) -> str:
    return f"e{int(min_edge)}_c{int(round(min_conf * 100))}_m{int(round(min_model * 100))}"


def default_profile_grid(*, subset: bool = False) -> list[SimProfile]:
    edges = [3.0, 5.0, 8.0] if subset else [3.0, 5.0, 8.0, 12.0]
    confs = [0.55, 0.65] if subset else [0.55, 0.65, 0.75]
    models = [0.55, 0.60] if subset else [0.55, 0.60, 0.65]
    out: list[SimProfile] = []
    for e in edges:
        for c in confs:
            for m in models:
                out.append(
                    SimProfile(
                        id=profile_id(e, c, m),
                        min_edge_cents=e,
                        min_confidence=c,
                        min_model_prob=m,
                    )
                )
    return out


def high_conviction_profiles() -> list[SimProfile]:
    """Consensus-only sim profiles for WC + Kalshi stack."""
    return [
        SimProfile(
            id="e8_c70_m65",
            min_edge_cents=8.0,
            min_confidence=0.70,
            min_model_prob=0.65,
            strategy_ids=("consensus",),
        ),
    ]
