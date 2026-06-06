from __future__ import annotations

from typing import ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.explain import direction_label
from ploy_agent.common.scoring import edge_cents as calc_edge
from ploy_agent.reasoning.claude_confidence import confidence_and_reasoning
from ploy_agent.reasoning.model import align_prob_to_yes, predict_home_win_prob
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult


def baseline_sentence(
    p_yes: float, mid: float, edge_cents: float,
    away_team: str, away_score: int, home_team: str, home_score: int,
    period: int | None, possession: str | None,
) -> str:
    """One concise, fact-only sentence grounded in the live game state."""
    clock = f", P{period}" if period else ""
    poss = f", {possession} ball" if possession in ("home", "away") else ""
    return (
        f"{direction_label(edge_cents)}: model puts Yes at {p_yes:.2f} vs market {mid:.2f} "
        f"({abs(edge_cents):.1f}¢ edge), from {away_team} {away_score}–{home_score} {home_team}"
        f"{clock}{poss}."
    )


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
        # LLM (if configured) supplies ONLY the numeric confidence; its free-text
        # narrative is discarded so no unverifiable claim reaches the user.
        conf, _llm_reasoning, _llm_sources = await confidence_and_reasoning(
            question=ctx.mrow.get("question"),
            model_prob=p_yes,
            market_prob=mid,
            edge_cents=edge,
            game_summary=summary,
            depth_1c=ctx.depth_1c,
            spread=ctx.spread,
        )
        reasoning = baseline_sentence(
            p_yes=p_yes, mid=mid, edge_cents=edge,
            away_team=away_team, away_score=int(gs.get("away_score") or 0),
            home_team=home_team, home_score=int(gs.get("home_score") or 0),
            period=int(gs.get("period") or 0) or None,
            possession=gs.get("possession"),
        )
        return StrategyResult(
            model_prob=p_yes,
            market_prob=mid,
            edge_cents=edge,
            confidence=conf,
            reasoning=reasoning,
            sources=[{"type": "game_state", "detail": summary}],
            signal_json={"kind": "baseline_logit"},
        )
