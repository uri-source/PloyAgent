from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.scoring import edge_cents as calc_edge
from ploy_agent.reasoning.model import align_prob_to_yes, predict_home_win_prob
from ploy_agent.reasoning import repo as rrepo
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult


class BehaviorFadeStrategy(Strategy):
    id: ClassVar[str] = "behavior_fade"

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        gid = str(ctx.game_state.get("game_id") or "")
        if not gid:
            return None
        events = await rrepo.recent_game_events(ctx.conn, gid, settings.behavior_price_window_sec + 120)
        swing = next((e for e in events if e["event_type"] == "score_swing"), None)
        if not swing:
            return None
        payload = swing["payload_json"]
        if isinstance(payload, str):
            import json

            payload = json.loads(payload)
        if not isinstance(payload, dict):
            return None
        prev_h = payload.get("prev_home")
        prev_a = payload.get("prev_away")
        if prev_h is None or prev_a is None:
            return None

        gs = ctx.game_state
        home_team = str(gs.get("home_team") or "")
        away_team = str(gs.get("away_team") or "")
        ev_ts = swing["ts"]
        if isinstance(ev_ts, datetime) and ev_ts.tzinfo is None:
            ev_ts = ev_ts.replace(tzinfo=timezone.utc)

        w = settings.behavior_price_window_sec
        first_m, last_m = await rrepo.mid_near_ts(ctx.conn, ctx.market_id, ev_ts, before_sec=2.0, after_sec=w)
        if first_m is None or last_m is None:
            return None
        price_delta = last_m - first_m

        p_before = predict_home_win_prob(
            ctx.model,
            home_score=int(prev_h),
            away_score=int(prev_a),
            period=int(gs.get("period") or 0) or None,
            possession=None,
            home_team=home_team,
            away_team=away_team,
        )
        p_after = predict_home_win_prob(
            ctx.model,
            home_score=int(gs["home_score"] or 0),
            away_score=int(gs["away_score"] or 0),
            period=int(gs["period"] or 0) or None,
            possession=gs.get("possession"),
            home_team=home_team,
            away_team=away_team,
        )
        yes_before = align_prob_to_yes(
            ctx.mrow.get("question"),
            home_team=home_team,
            away_team=away_team,
            p_home_wins=p_before,
        )
        yes_after = align_prob_to_yes(
            ctx.mrow.get("question"),
            home_team=home_team,
            away_team=away_team,
            p_home_wins=p_after,
        )
        if yes_before is None or yes_after is None:
            return None
        model_delta = yes_after - yes_before
        if abs(price_delta) < 0.02:
            return None
        if abs(price_delta) <= settings.behavior_overreaction_mult * abs(model_delta) + 0.01:
            return None

        sign = 1.0 if price_delta >= 0 else -1.0
        fade_target = last_m - sign * min(abs(model_delta), 0.08)
        edge = calc_edge(fade_target, last_m)
        if abs(edge) < settings.min_edge_cents:
            return None
        reasoning = (
            f"Behavior fade: mid moved {price_delta:+.3f} vs model ΔYes {model_delta:+.3f} after score swing — "
            f"possible overshoot. Suggested anchor ~{fade_target:.3f} vs last mid {last_m:.3f}."
        )
        return StrategyResult(
            model_prob=fade_target,
            market_prob=last_m,
            edge_cents=edge,
            confidence=0.55,
            reasoning=reasoning,
            sources=[{"type": "score_swing", "detail": str(payload)}],
            signal_json={
                "price_delta": price_delta,
                "model_delta": model_delta,
                "first_mid": first_m,
                "last_mid": last_m,
            },
        )
