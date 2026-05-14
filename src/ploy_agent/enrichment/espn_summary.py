from __future__ import annotations

from typing import Any

import httpx

from ploy_agent.enrichment.sports import ESPN_LEAGUE_PATHS


def _summary_url(league_key: str) -> str:
    lk = (league_key or "nba").strip().lower()
    path = ESPN_LEAGUE_PATHS.get(lk) or ESPN_LEAGUE_PATHS["nba"]
    return f"https://site.api.espn.com/apis/site/v2/sports/{path}/summary"


def _names_from_roster(roster: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(roster, list):
        return out
    for entry in roster:
        if isinstance(entry, dict):
            ath = entry.get("athlete") or entry
            if isinstance(ath, dict):
                nm = ath.get("displayName") or ath.get("fullName")
                if nm:
                    out.append(str(nm))
        elif isinstance(entry, str):
            out.append(entry)
    return out


async def fetch_roster_names(
    client: httpx.AsyncClient, game_id: str, *, league_key: str = "nba"
) -> tuple[list[str], list[str]]:
    """Best-effort active roster names from ESPN summary (may be empty)."""
    url = _summary_url(league_key)
    try:
        r = await client.get(url, params={"event": game_id}, timeout=25.0)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return [], []

    home_names: list[str] = []
    away_names: list[str] = []

    box = data.get("boxscore") or {}
    teams = box.get("teams") or []
    for side in teams:
        team = side.get("team") or {}
        side_label = str(team.get("homeAway") or "")
        roster = side.get("statistics") or []
        names: list[str] = []
        for stat in roster if isinstance(roster, list) else []:
            athletes = stat.get("athletes") if isinstance(stat, dict) else None
            if athletes:
                names.extend(_names_from_roster(athletes))
        if not names:
            athletes = side.get("athletes") or side.get("roster") or []
            names = _names_from_roster(athletes)
        if side_label == "home":
            home_names = names or home_names
        elif side_label == "away":
            away_names = names or away_names

    comps = (data.get("header") or {}).get("competitions") or data.get("competitions") or []
    if comps and (not home_names or not away_names):
        for c in comps[:1]:
            for comp in c.get("competitors") or []:
                side = str(comp.get("homeAway") or "")
                team = comp.get("team") or {}
                roster = comp.get("roster") or []
                names = _names_from_roster(roster)
                if side == "home" and not home_names:
                    home_names = names
                if side == "away" and not away_names:
                    away_names = names

    return home_names[:30], away_names[:30]
