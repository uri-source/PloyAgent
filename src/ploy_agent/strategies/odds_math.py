from __future__ import annotations


def american_to_implied_prob(american: float) -> float | None:
    """Convert American odds to implied probability including vig.

    Valid American odds are >= +100 or <= -100.  Values between -100 and +100
    (exclusive) are not valid.  Returns None for invalid input.
    """
    o = float(american)
    if o >= 100:
        return 100.0 / (o + 100.0)
    if o <= -100:
        return (-o) / ((-o) + 100.0)
    # Invalid American odds (between -100 and +100, including 0)
    return None


def devig_two_way(implied_a: float, implied_b: float) -> tuple[float, float]:
    s = implied_a + implied_b
    if s <= 0:
        return 0.5, 0.5
    return implied_a / s, implied_b / s


def norm_team(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum() or c.isspace()).strip()
