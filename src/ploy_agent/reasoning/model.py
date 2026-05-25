from __future__ import annotations

import json
import math
import re
from importlib import resources
from pathlib import Path
from typing import Any


def _word_in(needle: str, haystack: str) -> bool:
    """Check if needle appears as a whole word (or at a word boundary) in haystack."""
    if not needle:
        return False
    return bool(re.search(r'\b' + re.escape(needle) + r'\b', haystack, re.IGNORECASE))


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
    # Quarter-based elapsed fraction: Q1=0.0, Q2=0.25, Q3=0.5, Q4=0.75
    if period_n <= 4:
        elapsed_frac = max(0.0, (period_n - 1) / 4.0)
        remaining = reg * (1.0 - elapsed_frac)
    else:
        # OT: treat as small remaining time (5-min OT = 300s)
        ot_seconds = 300.0
        remaining = ot_seconds
    poss = 0.0
    if possession:
        pl = possession.lower()
        if _word_in(home_team, pl):
            poss = 1.0
        elif _word_in(away_team, pl):
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
        if _word_in(ht, tail):
            return p_home_wins
        if _word_in(at, tail):
            return 1.0 - p_home_wins
    h_match = re.search(r'\b' + re.escape(ht) + r'\b', q)
    a_match = re.search(r'\b' + re.escape(at) + r'\b', q)
    hi = h_match.start() if h_match else -1
    ai = a_match.start() if a_match else -1
    if hi >= 0 and (ai < 0 or hi <= ai):
        return p_home_wins
    if ai >= 0:
        return 1.0 - p_home_wins
    return None
