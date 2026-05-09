from __future__ import annotations

from typing import ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.scoring import edge_cents as calc_edge
from ploy_agent.reasoning import repo as rrepo
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult


class CrossMarketArbStrategy(Strategy):
    id: ClassVar[str] = "cross_market_arb"

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        evid = ctx.mrow.get("gamma_event_id")
        if not evid:
            return None
        evid = str(evid)
        siblings = await rrepo.sibling_market_mids(ctx.conn, evid, ctx.market_id)
        if len(siblings) < 1:
            return None
        mid_self = ctx.mid
        best: tuple[str, float, float] | None = None
        for other_id, mid_o in siblings:
            dev = abs(mid_self + mid_o - 1.0)
            if dev > settings.cross_market_sum_deviation:
                if best is None or dev > best[2]:
                    best = (other_id, mid_o, dev)
        if best is None:
            return None
        other_id, mid_o, dev = best
        fair_self = 1.0 - mid_o
        edge = calc_edge(fair_self, mid_self)
        if abs(edge) < settings.min_edge_cents:
            return None
        reasoning = (
            f"Cross-market inconsistency vs sibling `{other_id}`: mids sum to {mid_self + mid_o:.3f} "
            f"(deviation {dev:.3f} from 1.0). Implied fair for this contract ~{fair_self:.3f} vs mid {mid_self:.3f}."
        )
        return StrategyResult(
            model_prob=fair_self,
            market_prob=mid_self,
            edge_cents=edge,
            confidence=0.68,
            reasoning=reasoning,
            sources=[{"type": "sibling_market", "detail": other_id}],
            signal_json={"sibling_id": other_id, "sibling_mid": mid_o, "sum_deviation": dev},
        )
