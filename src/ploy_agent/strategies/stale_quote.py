from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.scoring import edge_cents as calc_edge
from ploy_agent.reasoning import repo as rrepo
from ploy_agent.reasoning.model import align_prob_to_yes, predict_home_win_prob
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult


class StaleQuoteStrategy(Strategy):
    id: ClassVar[str] = "stale_quote"

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        gid = str(ctx.game_state.get("game_id") or "")
        if not gid:
            return None
        events = await rrepo.latest_material_events(
            ctx.conn, gid, settings.stale_quote_window_sec + 60
        )
        if not events:
            return None
        ev = events[0]
        ev_ts = ev["ts"]
        if isinstance(ev_ts, datetime) and ev_ts.tzinfo is None:
            ev_ts = ev_ts.replace(tzinfo=timezone.utc)

        window_end = ev_ts + timedelta(seconds=settings.stale_quote_window_sec)
        rng = await rrepo.price_move_range(ctx.conn, ctx.market_id, ev_ts, window_end)
        if rng is None:
            rng = 0.0
        if rng >= settings.stale_quote_price_move:
            return None

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

        conf = min(0.95, 0.65 + 0.05 * (settings.stale_quote_price_move - rng) / max(1e-6, settings.stale_quote_price_move))
        reasoning = (
            f"Stale quote signal: material score swing detected but mid moved only ~{rng:.3f} "
            f"within {settings.stale_quote_window_sec:.0f}s (threshold {settings.stale_quote_price_move:.3f}). "
            f"Model-implied Yes {p_yes:.3f} vs mid {mid:.3f}."
        )
        return StrategyResult(
            model_prob=p_yes,
            market_prob=mid,
            edge_cents=edge,
            confidence=conf,
            reasoning=reasoning,
            sources=[
                {
                    "type": "game_event",
                    "detail": json.dumps(ev["payload_json"])
                    if isinstance(ev["payload_json"], dict)
                    else str(ev["payload_json"]),
                }
            ],
            signal_json={
                "event_ts": ev_ts.isoformat(),
                "price_range": rng,
                "payload": ev["payload_json"],
            },
        )
