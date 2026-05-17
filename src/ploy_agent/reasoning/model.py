from __future__ import annotations

import json
import math
from importlib import resources
from pathlib import Path
from typing import Any


def _sigmoid(z: float) -> float:
    if z >= 30:
        return 1.0
    if z <= -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def load_model(path: Path | None = None) -> dict[str, Any]:
    if path and path.exists():
        return json.loads(path.read_text())
    raw = resources.files("ploy_agent.reasoning").joinpath("default_model.json").read_text()
    return json.loads(raw)


def predict_home_win_prob(
    model: dict[str, Any],
    *,
    home_score: int,
    away_score: int,
    period: int | None,
    possession: str | None,
    home_team: str,
    away_team: str,
) -> float:
    diff = float(home_score - away_score)
    reg = float(model.get("regulation_seconds") or 2880)
    period_n = int(period or 1)
    # Quarter-based elapsed fraction: Q1=0.0, Q2=0.25, Q3=0.5, Q4=0.75, OT=1.0
    elapsed_frac = min(1.0, max(0.0, (period_n - 1) / 4.0))
    remaining = reg * (1.0 - elapsed_frac)
    poss = 0.0
    if possession:
        pl = possession.lower()
        if home_team.lower() in pl:
            poss = 1.0
        elif away_team.lower() in pl:
            poss = -1.0
    z = float(model.get("intercept", 0.0))
    # Use raw diff — normalizing by total score overweights early-game leads
    z += float(model.get("coef_diff", 0.0)) * diff
    z += float(model.get("coef_time", 0.0)) * (remaining / reg)
    z += float(model.get("coef_poss", 0.0)) * poss
    return float(_sigmoid(z))


def align_prob_to_yes(
    question: str | None,
    *,
    home_team: str,
    away_team: str,
    p_home_wins: float,
) -> float | None:
    """Map P(home wins) to P(Yes) using naive team mention heuristics."""
    if not question:
        return None
    q = question.lower().replace("?", " ")
    ht, at = home_team.lower(), away_team.lower()
    if " beat " in q:
        before = q.split(" beat ", 1)[0]
        tail = " ".join(before.strip().split()[-3:]).lower()
        if ht in tail:
            return p_home_wins
        if at in tail:
            return 1.0 - p_home_wins
    hi = q.find(ht)
    ai = q.find(at)
    if hi >= 0 and (ai < 0 or hi <= ai):
        return p_home_wins
    if ai >= 0:
        return 1.0 - p_home_wins
    return None
