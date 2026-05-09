from __future__ import annotations

from ploy_agent.enrichment.sports import LiveGame


def _match_tokens(question_lower: str, game: LiveGame, side: str) -> bool:
    if side == "home":
        names = (game.home_team,) + tuple(game.home_aliases)
    else:
        names = (game.away_team,) + tuple(game.away_aliases)
    for raw in names:
        t = raw.strip().lower()
        if len(t) >= 3 and t in question_lower:
            return True
    return False


def match_market_to_game(question: str | None, games: list[LiveGame]) -> str | None:
    if not question:
        return None
    q = question.lower()
    for g in games:
        if not g.home_team or not g.away_team:
            continue
        if _match_tokens(q, g, "home") and _match_tokens(q, g, "away"):
            return g.game_id
    return None
