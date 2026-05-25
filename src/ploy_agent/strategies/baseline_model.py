from __future__ import annotations

from typing import ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.scoring import edge_cents as calc_edge
from ploy_agent.reasoning.claude_confidence import confidence_and_reasoning
from ploy_agent.reasoning.model import align_prob_to_yes, predict_home_win_prob
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult


class BaselineModelStrategy(Strategy):
    id: ClassVar[str] = "baseline_model"

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        cat = str(ctx.mrow.get("category") or "").strip().lower()
        if cat not in settings.baseline_model_category_set():
            return None
        gs = ctx.game_state
        home_team = str(gs.get("home_team") or "")
        away_team = str(gs.get("away_team") or "")
        if not home_team or not away_team:
            return None
        mid = ctx.mid
        p_home = predict_home_win_prob(
            ctx.model,
            home_score=int(gs.get("home_score") or 0),
            away_score=int(gs.get("away_score") or 0),
            period=int(gs.get("period") or 0) or None,
            possession=gs.get("possession"),
            home_team=home_team,
            away_team=away_team,
        )
        p_yes = align_prob_to_yes(
            ctx.mrow.get("question"),
            home_team=home_team,
            away_team=away_team,
            p_home_wins=p_home,
        )
        if p_yes is None:
            return None
        edge = calc_edge(p_yes, mid)
        if abs(edge) < settings.min_edge_cents:
            return None
        summary = (
            f"{away_team}@{home_team} {gs.get('away_score')}-{gs.get('home_score')} "
            f"P{gs.get('period')} poss={gs.get('possession')}"
        )
        conf, reasoning, sources = await confidence_and_reasoning(
            question=ctx.mrow.get("question"),
            model_prob=p_yes,
            market_prob=mid,
            edge_cents=edge,
            game_summary=summary,
            depth_1c=ctx.depth_1c,
            spread=ctx.spread,
        )
        return StrategyResult(
            model_prob=p_yes,
            market_prob=mid,
            edge_cents=edge,
            confidence=conf,
            reasoning=reasoning,
            sources=sources,
            signal_json={"kind": "baseline_logit"},
        )
