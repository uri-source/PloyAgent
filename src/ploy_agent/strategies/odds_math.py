from __future__ import annotations


def american_to_implied_prob(american: float) -> float:
    """Convert American odds to implied probability including vig."""
    o = float(american)
    if o > 0:
        return 100.0 / (o + 100.0)
    return (-o) / ((-o) + 100.0)


def devig_two_way(implied_a: float, implied_b: float) -> tuple[float, float]:
    s = implied_a + implied_b
    if s <= 0:
        return 0.5, 0.5
    return implied_a / s, implied_b / s


def norm_team(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum() or c.isspace()).strip()
