from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from ploy_agent.common.config import settings

# Short league keys (config ENRICHMENT_ESPN_LEAGUES) -> ESPN site API path under /sports/
ESPN_LEAGUE_PATHS: dict[str, str] = {
    "nba": "basketball/nba",
    "mlb": "baseball/mlb",
    "wnba": "basketball/wnba",
    "nfl": "football/nfl",
    "nhl": "hockey/nhl",
}


@dataclass
class LiveGame:
    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int | None
    time_remaining: str | None
    possession: str | None
    completed: bool
    home_aliases: tuple[str, ...] = field(default_factory=tuple)
    away_aliases: tuple[str, ...] = field(default_factory=tuple)
    league: str = ""

    def espn_summary_league_key(self) -> str:
        """League key for ESPN summary/roster URLs (EspnMultiLeagueProvider games)."""
        return (self.league or "nba").strip().lower() or "nba"


class SportsProvider(ABC):
    @abstractmethod
    async def fetch_live_games(self, client: httpx.AsyncClient) -> list[LiveGame]:
        raise NotImplementedError


def _safe_int(val: Any) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _team_tokens(team_obj: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    team = team_obj.get("team") or {}
    display = str(team.get("displayName") or "").strip()
    short = str(team.get("shortDisplayName") or "").strip()
    abbr = str(team.get("abbreviation") or "").strip()
    primary = display or short or abbr or "Unknown"
    aliases: list[str] = []
    for x in (display, short, abbr):
        if x and x not in aliases:
            aliases.append(x)
    return primary, tuple(aliases)


def _parse_espn_scoreboard_payload(data: dict[str, Any], league_key: str) -> list[LiveGame]:
    events = data.get("events") or []
    out: list[LiveGame] = []
    lk = league_key.strip().lower()
    for ev in events:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors") or []
        home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home_c or not away_c:
            continue
        home_name, home_aliases = _team_tokens(home_c)
        away_name, away_aliases = _team_tokens(away_c)
        try:
            home_score = int(str(home_c.get("score") or "0"))
        except ValueError:
            home_score = 0
        try:
            away_score = int(str(away_c.get("score") or "0"))
        except ValueError:
            away_score = 0
        status = comp.get("status") or {}
        stype = status.get("type") or {}
        completed = bool(stype.get("completed"))
        period = status.get("period")
        if period is not None:
            try:
                period = int(period)
            except (TypeError, ValueError):
                period = None
        clock = status.get("displayClock") or stype.get("shortDetail")
        gid = str(ev.get("id") or comp.get("id") or "")
        if not gid:
            continue
        out.append(
            LiveGame(
                game_id=gid,
                home_team=home_name,
                away_team=away_name,
                home_score=home_score,
                away_score=away_score,
                period=period,
                time_remaining=str(clock) if clock else None,
                possession=None,
                completed=completed,
                home_aliases=home_aliases,
                away_aliases=away_aliases,
                league=lk,
            )
        )
    return out


class EspnMultiLeagueProvider(SportsProvider):
    """Public ESPN scoreboard JSON per configured league (no API key)."""

    async def fetch_live_games(self, client: httpx.AsyncClient) -> list[LiveGame]:
        out: list[LiveGame] = []
        for league_key in settings.enrichment_espn_league_keys():
            path = ESPN_LEAGUE_PATHS.get(league_key)
            if not path:
                continue
            url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
            r = await client.get(url, timeout=30.0)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                out.extend(_parse_espn_scoreboard_payload(data, league_key))
        return out


def _league_hint_from_odds_sport(sport: str) -> str:
    s = sport.lower()
    if "mlb" in s or "baseball_mlb" in s:
        return "mlb"
    if "wnba" in s:
        return "wnba"
    if "nfl" in s:
        return "nfl"
    if "nhl" in s:
        return "nhl"
    return "nba"


class OddsApiProvider(SportsProvider):
    async def fetch_live_games(self, client: httpx.AsyncClient) -> list[LiveGame]:
        if not settings.odds_api_key:
            return []
        base = settings.odds_api_base.rstrip("/")
        out: list[LiveGame] = []
        for sport in settings.enrichment_odds_sport_keys():
            url = f"{base}/sports/{sport}/scores"
            r = await client.get(
                url,
                params={"daysFrom": 2, "apiKey": settings.odds_api_key},
                timeout=30.0,
            )
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                continue
            league = _league_hint_from_odds_sport(sport)
            for g in data:
                scores = g.get("scores") or []
                home_team = str(g.get("home_team") or "")
                away_team = str(g.get("away_team") or "")
                home_s = next(
                    (_safe_int(s.get("score")) for s in scores if s.get("name") == home_team), 0
                )
                away_s = next(
                    (_safe_int(s.get("score")) for s in scores if s.get("name") == away_team), 0
                )
                out.append(
                    LiveGame(
                        game_id=str(g.get("id")),
                        home_team=home_team,
                        away_team=away_team,
                        home_score=home_s,
                        away_score=away_s,
                        period=int(g.get("period") or 0) or None,
                        time_remaining=g.get("last_update"),
                        possession=None,
                        completed=bool(g.get("completed")),
                        home_aliases=(home_team,) if home_team else (),
                        away_aliases=(away_team,) if away_team else (),
                        league=league,
                    )
                )
        return out


def get_provider() -> SportsProvider:
    p = settings.sports_provider.lower()
    if p in ("odds", "theodds", "oddsapi"):
        return OddsApiProvider()
    return EspnMultiLeagueProvider()
