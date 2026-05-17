from __future__ import annotations

from typing import ClassVar

from ploy_agent.common.confidence import statistical_confidence
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
        if not siblings:
            return None

        mid_self = ctx.mid

        if len(siblings) == 1:
            other_id, mid_o = siblings[0]
            dev = abs(mid_self + mid_o - 1.0)
            if dev <= settings.cross_market_sum_deviation:
                return None
            fair_self = 1.0 - mid_o
            edge = calc_edge(fair_self, mid_self)
            if abs(edge) < settings.min_edge_cents:
                return None
            conf, conf_reasoning = statistical_confidence(
                depth_1c=ctx.depth_1c,
                spread=ctx.spread,
                n_siblings=1,
                sum_deviation=dev,
                edge_cents=edge,
                mid=mid_self,
                is_binary_pair=True,
            )
            reasoning = (
                f"Binary pair inconsistency vs `{other_id}`: mids sum to {mid_self + mid_o:.3f} "
                f"(deviation {dev:.3f} from 1.0). Implied fair ~{fair_self:.3f} vs mid {mid_self:.3f}. "
                f"{conf_reasoning}"
            )
            return StrategyResult(
                model_prob=fair_self,
                market_prob=mid_self,
                edge_cents=edge,
                confidence=conf,
                reasoning=reasoning,
                sources=[{"type": "sibling_market", "detail": other_id}],
                signal_json={"sibling_id": other_id, "sibling_mid": mid_o, "sum_deviation": dev},
            )

        total_mid = mid_self + sum(m for _, m in siblings)
        if total_mid > 1.5 or total_mid < 0.5:
            return None
        dev = abs(total_mid - 1.0)
        if dev <= settings.cross_market_sum_deviation:
            return None

        # Multi-outcome arb: find which outcome is most mispriced.
        # Simple normalization (mid/total) always produces SELL when overround >1.
        # Instead, compare this outcome's share of overround vs siblings.
        # If ALL outcomes are equally overpriced, there's no actionable signal
        # for any single outcome — skip.
        n_outcomes = len(siblings) + 1
        overround = total_mid - 1.0  # positive = overround, negative = underround

        # Each outcome's "fair share" of the deviation
        fair_share_dev = overround * (mid_self / total_mid) if total_mid > 0 else 0
        # This outcome's actual deviation from its normalized value
        fair_self = mid_self / total_mid if total_mid > 0 else mid_self
        outcome_dev = mid_self - fair_self  # how much THIS outcome is inflated

        # Only signal if this outcome absorbs a disproportionate share of the
        # overround — i.e., it's MORE mispriced than its proportional share.
        # The threshold: this outcome's deviation must exceed 1.5× its fair share.
        if abs(fair_share_dev) > 0 and abs(outcome_dev) < abs(fair_share_dev) * 1.5:
            return None

        # For underround (total < 1.0), the market is underpriced overall,
        # so normalization UP correctly produces BUY signals.
        edge = calc_edge(fair_self, mid_self)
        if abs(edge) < settings.min_edge_cents:
            return None
        conf, conf_reasoning = statistical_confidence(
            depth_1c=ctx.depth_1c,
            spread=ctx.spread,
            n_siblings=len(siblings),
            sum_deviation=dev,
            edge_cents=edge,
            mid=mid_self,
            is_binary_pair=False,
        )
        direction = "overpriced (SELL)" if edge < 0 else "underpriced (BUY)"
        reasoning = (
            f"Multi-outcome arb: {n_outcomes} sibling markets sum to {total_mid:.3f} "
            f"(deviation {dev:.3f} from 1.0). This outcome is disproportionately {direction}. "
            f"Normalized fair ~{fair_self:.3f} vs mid {mid_self:.3f}. "
            f"{conf_reasoning}"
        )
        return StrategyResult(
            model_prob=fair_self,
            market_prob=mid_self,
            edge_cents=edge,
            confidence=conf,
            reasoning=reasoning,
            sources=[{"type": "multi_outcome_arb", "detail": f"{n_outcomes} markets"}],
            signal_json={
                "total_mid": total_mid,
                "n_siblings": len(siblings),
                "sum_deviation": dev,
                "outcome_dev_share": round(outcome_dev / fair_share_dev, 2)
                if fair_share_dev else 0,
            },
        )
