from __future__ import annotations

from typing import ClassVar

from ploy_agent.common.confidence import statistical_confidence
from ploy_agent.common.config import settings
from ploy_agent.common.explain import direction_label
from ploy_agent.common.scoring import edge_cents as calc_edge
from ploy_agent.reasoning import repo as rrepo
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult


def _short(question: str | None, fallback: str, limit: int = 60) -> str:
    """Human-readable market label: trimmed question, falling back to the id."""
    if not question:
        return fallback
    q = question.strip()
    return q if len(q) <= limit else q[: limit - 1] + "…"


def pair_sentence(
    self_label: str, mid_self: float, other_label: str, mid_o: float,
    fair_self: float, edge_cents: float,
) -> str:
    """One concise, fact-only sentence for a binary (two-outcome) arb."""
    pair_sum = mid_self + mid_o
    off = abs(pair_sum - 1.0) * 100
    return (
        f"{direction_label(edge_cents)}: “{self_label}” at {mid_self:.2f} is mispriced against "
        f"its complement “{other_label}” at {mid_o:.2f} — the pair sums to {pair_sum:.2f} "
        f"(off 1.00 by {off:.1f}¢), implying fair ≈{fair_self:.2f} ({abs(edge_cents):.1f}¢ edge)."
    )


def multi_sentence(
    self_label: str, mid_self: float, n_outcomes: int, total_mid: float,
    fair_self: float, edge_cents: float,
) -> str:
    """One concise, fact-only sentence for a multi-outcome arb."""
    off = abs(total_mid - 1.0) * 100
    return (
        f"{direction_label(edge_cents)}: “{self_label}” at {mid_self:.2f} is the most-mispriced of "
        f"{n_outcomes} mutually-exclusive outcomes summing to {total_mid:.2f} (off 1.00 by "
        f"{off:.1f}¢), implying normalized fair ≈{fair_self:.2f} ({abs(edge_cents):.1f}¢ edge)."
    )


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
        self_label = _short(ctx.mrow.get("question"), ctx.market_id)

        if len(siblings) == 1:
            other_id, mid_o, other_q = siblings[0]
            other_label = _short(other_q, other_id)
            dev = abs(mid_self + mid_o - 1.0)
            if dev <= settings.cross_market_sum_deviation:
                return None
            fair_self = 1.0 - mid_o
            edge = calc_edge(fair_self, mid_self)
            if abs(edge) < settings.min_edge_cents:
                return None
            conf, _ = statistical_confidence(
                depth_1c=ctx.depth_1c,
                spread=ctx.spread,
                n_siblings=1,
                sum_deviation=dev,
                edge_cents=edge,
                mid=mid_self,
                is_binary_pair=True,
            )
            reasoning = pair_sentence(
                self_label, mid_self, other_label, mid_o, fair_self, edge
            )
            return StrategyResult(
                model_prob=fair_self,
                market_prob=mid_self,
                edge_cents=edge,
                confidence=conf,
                reasoning=reasoning,
                sources=[
                    {"type": "sibling_market", "detail": f"{other_label} ({other_id})"}
                ],
                signal_json={
                    "sibling_id": other_id,
                    "sibling_question": other_q,
                    "sibling_mid": mid_o,
                    "sum_deviation": dev,
                    "markets": [
                        {"id": ctx.market_id, "question": ctx.mrow.get("question"), "mid": mid_self},
                        {"id": other_id, "question": other_q, "mid": mid_o},
                    ],
                },
            )

        total_mid = mid_self + sum(m for _, m, _ in siblings)
        if total_mid > 1.5 or total_mid < 0.5:
            return None
        dev = abs(total_mid - 1.0)
        if dev <= settings.cross_market_sum_deviation:
            return None

        # Multi-outcome arb: check if this outcome deviates more than siblings.
        # Use rank-based approach: only signal the most-mispriced outcome(s).
        n_outcomes = len(siblings) + 1

        # Normalized fair value for each outcome
        fair_self = mid_self / total_mid if total_mid > 0 else mid_self

        # Readable label per market id (this outcome + every sibling)
        label_by_id = {ctx.market_id: _short(ctx.mrow.get("question"), ctx.market_id)}
        for sid, _smid, sq in siblings:
            label_by_id[sid] = _short(sq, sid)

        # Collect all mids and compute each outcome's edge vs its fair share
        all_mids = [(ctx.market_id, mid_self)] + [(sid, smid) for sid, smid, _ in siblings]
        edges = []
        for oid, omid in all_mids:
            ofair = omid / total_mid if total_mid > 0 else omid
            edges.append((oid, abs(omid - ofair)))

        # Sort by absolute edge descending — only signal if this market
        # is in the top quartile of mispricing among siblings
        edges.sort(key=lambda x: x[1], reverse=True)
        top_cutoff = max(1, n_outcomes // 4)
        top_ids = {oid for oid, _ in edges[:top_cutoff]}
        if ctx.market_id not in top_ids:
            return None

        # For underround (total < 1.0), the market is underpriced overall,
        # so normalization UP correctly produces BUY signals.
        edge = calc_edge(fair_self, mid_self)
        if abs(edge) < settings.min_edge_cents:
            return None
        conf, _ = statistical_confidence(
            depth_1c=ctx.depth_1c,
            spread=ctx.spread,
            n_siblings=len(siblings),
            sum_deviation=dev,
            edge_cents=edge,
            mid=mid_self,
            is_binary_pair=False,
        )
        reasoning = multi_sentence(
            label_by_id[ctx.market_id], mid_self, n_outcomes, total_mid, fair_self, edge
        )
        return StrategyResult(
            model_prob=fair_self,
            market_prob=mid_self,
            edge_cents=edge,
            confidence=conf,
            reasoning=reasoning,
            sources=[
                {"type": "multi_outcome_arb", "detail": label_by_id[oid]}
                for oid, _ in all_mids
                if oid != ctx.market_id
            ],
            signal_json={
                "total_mid": total_mid,
                "n_siblings": len(siblings),
                "sum_deviation": dev,
                "this_edge_cents": round(edge, 2),
                "markets": [
                    {"id": oid, "question": label_by_id[oid], "mid": round(omid, 3)}
                    for oid, omid in all_mids
                ],
            },
        )
