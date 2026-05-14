from __future__ import annotations

from typing import Any, ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.odds_sports import odds_sport_key_for_category
from ploy_agent.common.scoring import edge_cents as calc_edge
from ploy_agent.reasoning.model import align_prob_to_yes
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.odds_math import american_to_implied_prob, devig_two_way, norm_team
from ploy_agent.strategies.types import StrategyContext, StrategyResult


def _implied_for_team(by_name: dict[str, float], team: str) -> float | None:
    nt = norm_team(team)
    if len(nt) < 2:
        return None
    for k, v in by_name.items():
        nk = norm_team(k)
        if nk == nt or nt in nk or nk in nt:
            return v
        if len(nt) >= 4 and (nt[:4] in nk or nk[:4] in nt):
            return v
    return None


class SportsbookConsensusStrategy(Strategy):
    id: ClassVar[str] = "sportsbook_consensus"
    requires: ClassVar[frozenset[str]] = frozenset({"odds_api"})

    def _match_event(
        self, games: list[dict[str, Any]], gs: dict[str, Any]
    ) -> dict[str, Any] | None:
        ht = norm_team(str(gs.get("home_team") or ""))
        at = norm_team(str(gs.get("away_team") or ""))
        if len(ht) < 3 or len(at) < 3:
            return None
        for g in games:
            gh = norm_team(str(g.get("home_team") or ""))
            ga = norm_team(str(g.get("away_team") or ""))
            if (ht in gh or gh in ht) and (at in ga or ga in at):
                return g
            if (ht in ga or ga in ht) and (at in gh or gh in at):
                return g
        return None

    def _weighted_home_prob(
        self, event: dict[str, Any]
    ) -> tuple[float | None, list[dict[str, Any]]]:
        home_name = str(event.get("home_team") or "")
        away_name = str(event.get("away_team") or "")
        weights = settings.sharp_book_weights()
        bookmakers = event.get("bookmakers") or []
        weighted_home: list[tuple[float, float]] = []
        breakdown: list[dict[str, Any]] = []
        for bk in bookmakers:
            key = str(bk.get("key") or "").lower()
            w = weights.get(key, 1.0)
            markets = bk.get("markets") or []
            h2h = next((m for m in markets if m.get("key") == "h2h"), None)
            if not h2h:
                continue
            outcomes = h2h.get("outcomes") or []
            by_name: dict[str, float] = {}
            for o in outcomes:
                name = str(o.get("name") or "")
                price = o.get("price")
                if price is None or not name:
                    continue
                by_name[name] = american_to_implied_prob(float(price))
            ih = _implied_for_team(by_name, home_name)
            ia = _implied_for_team(by_name, away_name)
            if ih is None or ia is None:
                continue
            fh, fa = devig_two_way(ih, ia)
            weighted_home.append((fh * w, w))
            breakdown.append({"book": key, "fair_home": fh, "weight": w})
        if not weighted_home:
            return None, breakdown
        num = sum(x for x, _ in weighted_home)
        den = sum(w for _, w in weighted_home)
        return num / den if den > 0 else None, breakdown

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        if not settings.odds_api_key:
            return None
        sport_key = odds_sport_key_for_category(ctx.mrow.get("category"))
        if not sport_key:
            return None
        regions = settings.sportsbook_regions.replace(" ", "")
        url = f"{settings.odds_api_base.rstrip('/')}/sports/{sport_key}/odds"
        r = await ctx.http.get(
            url,
            params={
                "regions": regions,
                "markets": "h2h",
                "oddsFormat": "american",
                "apiKey": settings.odds_api_key,
            },
            timeout=45.0,
        )
        r.raise_for_status()
        games = r.json()
        if not isinstance(games, list):
            return None
        ev = self._match_event(games, ctx.game_state)
        if not ev:
            return None
        p_home, breakdown = self._weighted_home_prob(ev)
        if p_home is None:
            return None
        gs = ctx.game_state
        home_team = str(gs.get("home_team") or "")
        away_team = str(gs.get("away_team") or "")
        p_yes = align_prob_to_yes(
            ctx.mrow.get("question"),
            home_team=home_team,
            away_team=away_team,
            p_home_wins=p_home,
        )
        if p_yes is None:
            return None
        mid = ctx.mid
        edge = calc_edge(p_yes, mid)
        if abs(edge) < settings.min_edge_cents:
            return None
        reasoning = (
            f"Sportsbook consensus (weighted h2h devig) implies Yes ~{p_yes:.3f} vs Polymarket mid {mid:.3f}. "
            f"Edge {edge:.1f}¢."
        )
        return StrategyResult(
            model_prob=p_yes,
            market_prob=mid,
            edge_cents=edge,
            confidence=0.72,
            reasoning=reasoning,
            sources=[{"type": "odds_api", "detail": str(ev.get("id"))}],
            signal_json={"books": breakdown[:12], "event_id": ev.get("id")},
        )
