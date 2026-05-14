from __future__ import annotations

"""Map Polymarket Gamma `markets.category` slugs to The Odds API v4 sport keys."""

# Short Gamma-style slugs -> Odds API /sports/{key}/odds
_CATEGORY_TO_ODDS_SPORT: dict[str, str] = {
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "wnba": "basketball_wnba",
    "nfl": "americanfootball_nfl",
    "nhl": "icehockey_nhl",
}


def odds_sport_key_for_category(category: str | None) -> str | None:
    """
    Return The Odds API sport key for h2h odds fetch, or None if unknown.
    Accepts Gamma category slugs (e.g. nba, mlb) or a full key already
    shaped like `basketball_nba`.
    """
    if not category:
        return None
    c = category.strip().lower()
    if not c:
        return None
    if c in _CATEGORY_TO_ODDS_SPORT:
        return _CATEGORY_TO_ODDS_SPORT[c]
    if "_" in c:
        return c
    return None
