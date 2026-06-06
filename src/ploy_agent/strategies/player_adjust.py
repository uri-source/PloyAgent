from __future__ import annotations

from typing import Any, ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.explain import direction_label
from ploy_agent.common.scoring import edge_cents as calc_edge
from ploy_agent.reasoning.model import align_prob_to_yes, predict_home_win_prob
from ploy_agent.reasoning import repo as rrepo
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult


def player_adjust_sentence(
    adj: float, dh: float, da: float, p_yes: float, mid: float, edge_cents: float,
) -> str:
    """One concise, fact-only sentence for the lineup-strength adjustment."""
    return (
        f"{direction_label(edge_cents)}: lineup-strength adjustment of {adj:+.3f} to home win "
        f"prob (active-player EPM: home {dh:+.2f} vs away {da:+.2f}) puts Yes at {p_yes:.2f} "
        f"vs market {mid:.2f} ({abs(edge_cents):.1f}¢ edge)."
    )


def _norm_keys(names: list[Any]) -> list[str]:
    out: list[str] = []
    for x in names:
        s = str(x).strip().lower()
        if s:
            out.append(s)
    return out


class PlayerAdjustStrategy(Strategy):
    id: ClassVar[str] = "player_adjust"

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        cat = str(ctx.mrow.get("category") or "").strip().lower()
        if cat not in settings.baseline_model_category_set():
            return None
        gid = str(ctx.game_state.get("game_id") or "")
        if not gid:
            return None
        lineup = await rrepo.latest_lineup(ctx.conn, gid)
        if not lineup:
            return None
        ha = _norm_keys(lineup.get("home_active") or [])
        aa = _norm_keys(lineup.get("away_active") or [])
        if not ha and not aa:
            return None
        dh = await rrepo.player_deltas_for_keys(ctx.conn, ha)
        da = await rrepo.player_deltas_for_keys(ctx.conn, aa)
        adj = settings.player_adjust_scale * (dh - da)

        gs = ctx.game_state
        home_team = str(gs.get("home_team") or "")
        away_team = str(gs.get("away_team") or "")
        p_home = predict_home_win_prob(
            ctx.model,
            home_score=int(gs.get("home_score") or 0),
            away_score=int(gs.get("away_score") or 0),
            period=int(gs.get("period") or 0) or None,
            possession=gs.get("possession"),
            home_team=home_team,
            away_team=away_team,
        )
        p_home_adj = max(0.02, min(0.98, p_home + adj))
        p_yes = align_prob_to_yes(
            ctx.mrow.get("question"),
            home_team=home_team,
            away_team=away_team,
            p_home_wins=p_home_adj,
        )
        if p_yes is None:
            return None
        mid = ctx.mid
        edge = calc_edge(p_yes, mid)
        if abs(edge) < settings.min_edge_cents:
            return None
        reasoning = player_adjust_sentence(adj, dh, da, p_yes, mid, edge)
        return StrategyResult(
            model_prob=p_yes,
            market_prob=mid,
            edge_cents=edge,
            confidence=0.62,
            reasoning=reasoning,
            sources=[{"type": "player_impact", "detail": "game_lineups"}],
            signal_json={"dh": dh, "da": da, "adj": adj},
        )
