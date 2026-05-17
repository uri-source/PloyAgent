from __future__ import annotations

from typing import ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.scoring import edge_cents as calc_edge
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult


class ConsensusStrategy(Strategy):
    """Ensemble strategy: boosts signal when 2+ independent strategies agree on direction.

    Reads recent fair_values for the same market from OTHER strategies.  If multiple
    strategies agree (all BUY or all SELL), emits a consensus signal with:
    - model_prob = confidence-weighted average of contributing model probs
    - confidence = boosted (geometric mean of individual × agreement bonus)
    - edge = recalculated from the consensus model_prob

    Free — uses data already in the DB from other strategy runs.
    Must run LAST in the strategy list so other strategies have written first.
    """

    id: ClassVar[str] = "consensus"

    # Minimum strategies that must agree for a consensus signal
    _MIN_AGREEING: int = 2
    # How recent the contributing fair_values must be (seconds)
    _LOOKBACK_SEC: float = 300.0
    # Confidence boost when strategies agree (multiplicative)
    _AGREEMENT_BONUS: float = 1.25

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        # Fetch recent fair_values for this market from OTHER strategies
        rows = await ctx.conn.fetch(
            """
            SELECT strategy_id, model_prob, market_prob, edge_cents, confidence
            FROM fair_values
            WHERE market_id = $1
              AND ts > NOW() - INTERVAL '1 second' * $2
              AND strategy_id <> $3
            ORDER BY ts DESC
            """,
            ctx.market_id,
            self._LOOKBACK_SEC,
            self.id,
        )
        if not rows:
            return None

        # Deduplicate: keep latest per strategy
        seen: dict[str, dict] = {}
        for r in rows:
            sid = r["strategy_id"]
            if sid not in seen:
                seen[sid] = {
                    "strategy_id": sid,
                    "model_prob": float(r["model_prob"]),
                    "market_prob": float(r["market_prob"]),
                    "edge_cents": float(r["edge_cents"]),
                    "confidence": float(r["confidence"]),
                }

        if len(seen) < self._MIN_AGREEING:
            return None

        # Check agreement: all must point the same direction
        signals = list(seen.values())
        buy_signals = [s for s in signals if s["edge_cents"] > 0]
        sell_signals = [s for s in signals if s["edge_cents"] < 0]

        # Pick the direction with more agreement
        if len(buy_signals) >= self._MIN_AGREEING:
            agreeing = buy_signals
        elif len(sell_signals) >= self._MIN_AGREEING:
            agreeing = sell_signals
        else:
            return None  # No consensus

        # Confidence-weighted average model_prob
        total_weight = sum(s["confidence"] for s in agreeing)
        if total_weight <= 0:
            return None
        consensus_prob = sum(
            s["model_prob"] * s["confidence"] for s in agreeing
        ) / total_weight

        # Consensus edge
        consensus_edge = calc_edge(consensus_prob, ctx.mid)
        if abs(consensus_edge) < settings.min_edge_cents:
            return None

        # Boosted confidence: average confidence × agreement bonus
        avg_conf = total_weight / len(agreeing)
        n_agreeing = len(agreeing)
        # More strategies agreeing = higher bonus (capped at 0.95)
        boost = self._AGREEMENT_BONUS + (n_agreeing - self._MIN_AGREEING) * 0.05
        consensus_conf = min(avg_conf * boost, 0.95)

        contributing = [s["strategy_id"] for s in agreeing]
        direction = "BUY" if consensus_edge > 0 else "SELL"
        edge_parts = ", ".join(
            f"{s['strategy_id']}={s['edge_cents']:+.1f}¢" for s in agreeing
        )
        reasoning = (
            f"Consensus {direction}: {n_agreeing} strategies agree — "
            f"{', '.join(contributing)}. "
            f"Weighted model_prob={consensus_prob:.3f} vs mid={ctx.mid:.3f} "
            f"(edge={consensus_edge:+.1f}¢). "
            f"Individual edges: {edge_parts}."
        )

        return StrategyResult(
            model_prob=consensus_prob,
            market_prob=ctx.mid,
            edge_cents=consensus_edge,
            confidence=consensus_conf,
            reasoning=reasoning,
            sources=[{"type": "consensus", "detail": sid} for sid in contributing],
            signal_json={
                "n_agreeing": n_agreeing,
                "contributing": contributing,
                "individual_edges": {s["strategy_id"]: s["edge_cents"] for s in agreeing},
                "boost": round(boost, 2),
            },
        )
