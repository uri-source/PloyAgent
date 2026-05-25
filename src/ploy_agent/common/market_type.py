"""Classify Polymarket questions into market types for analytics."""

from __future__ import annotations

import re

# Order matters — first match wins
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("over_under", re.compile(r"O/U\s+\d", re.IGNORECASE)),
    ("over_under", re.compile(r"over[/ ]under", re.IGNORECASE)),
    ("over_under", re.compile(r"total\s+(points|runs|goals|score)", re.IGNORECASE)),
    ("spread", re.compile(r"spread[:\s]", re.IGNORECASE)),
    ("spread", re.compile(r"\(\s*[+-]?\d+\.5\s*\)", re.IGNORECASE)),
    ("player_prop", re.compile(
        r"(points|rebounds|assists|strikeouts|home\s*runs|touchdowns|yards|hits|RBIs|saves|goals)"
        r"\s+(over|under|o/u|\d)",
        re.IGNORECASE,
    )),
    ("player_prop", re.compile(
        r"(will|does|can)\s+\w+\s+\w+\s+(score|hit|record|get|make|throw)", re.IGNORECASE
    )),
    ("winner", re.compile(r"(will|to)\s+.{0,40}\s+(win|beat|defeat)", re.IGNORECASE)),
    ("winner", re.compile(r"(moneyline|money\s*line|ML)\b", re.IGNORECASE)),
    ("winner", re.compile(r"\bvs\.?\s+", re.IGNORECASE)),
]


def classify_market_type(question: str | None) -> str:
    """Return one of: winner, spread, over_under, player_prop, other."""
    if not question:
        return "other"
    for label, pat in _PATTERNS:
        if pat.search(question):
            return label
    return "other"


def classify_sport_category(category: str | None, question: str | None) -> str:
    """Normalize sport category from Gamma category slug or question text."""
    if category:
        c = category.strip().lower()
        if c:
            return c

    if not question:
        return "unknown"
    q = question.lower()
    sport_hints = [
        ("nba", ["nba", "basketball", "lakers", "celtics", "warriors", "bucks", "nuggets",
                  "76ers", "knicks", "heat", "suns", "nets", "bulls", "clippers", "cavaliers",
                  "mavericks", "rockets", "spurs", "raptors", "hawks", "hornets", "wizards",
                  "pistons", "pacers", "magic", "grizzlies", "pelicans", "timberwolves",
                  "thunder", "blazers", "kings", "jazz"]),
        ("mlb", ["mlb", "baseball", "yankees", "red sox", "dodgers", "cubs", "astros",
                 "braves", "mets", "phillies", "padres", "cardinals", "brewers", "guardians",
                 "orioles", "rays", "mariners", "twins", "rangers", "blue jays", "royals",
                 "tigers", "white sox", "reds", "pirates", "nationals", "rockies", "marlins",
                 "athletics", "angels", "giants", "diamondbacks"]),
        ("nfl", ["nfl", "football", "chiefs", "eagles", "49ers", "cowboys", "bills",
                 "ravens", "bengals", "dolphins", "lions", "steelers", "jaguars",
                 "chargers", "broncos", "seahawks", "packers", "bears", "saints",
                 "falcons", "vikings", "colts", "texans", "commanders", "panthers",
                 "browns", "jets", "patriots", "titans", "raiders", "rams", "buccaneers"]),
        ("nhl", ["nhl", "hockey", "bruins", "avalanche", "hurricanes", "devils",
                 "oilers", "rangers", "maple leafs", "panthers", "stars", "wild",
                 "lightning", "penguins", "kraken", "flames", "canadiens",
                 "capitals", "islanders", "sabres", "canucks", "senators", "predators",
                 "blue jackets", "red wings", "blackhawks", "ducks", "sharks"]),
        ("wnba", ["wnba"]),
        ("soccer", ["soccer", "premier league", "la liga", "bundesliga", "serie a", "mls"]),
    ]
    for sport, keywords in sport_hints:
        for kw in keywords:
            if kw in q:
                return sport
    return "unknown"
