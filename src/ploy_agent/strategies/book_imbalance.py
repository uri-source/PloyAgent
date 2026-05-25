from __future__ import annotations

"""Order Book Imbalance strategy.

Uses bid/ask depth asymmetry from order_book_snapshots to infer directional pressure.
When buy-side depth significantly outweighs sell-side (or vice versa), the market is
likely to move in that direction — creating edge if the mid hasn't caught up yet.

Signal:
  imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth)
  If imbalance > threshold → price likely to rise → model_prob biased up
  If imbalance < -threshold → price likely to fall → model_prob biased down

No external APIs needed — uses order_book_snapshots already in DB.
"""

from typing import ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.logging_config import get_logger
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult

log = get_logger("strategies.book_imbalance")

# Minimum imbalance ratio to consider a signal
_IMBALANCE_THRESHOLD = 0.35
# How much to shift probability based on imbalance magnitude
_PROB_SHIFT_SCALE = 0.08
# Minimum total depth to trust the signal
_MIN_TOTAL_DEPTH = 500.0


class BookImbalanceStrategy(Strategy):
    id: ClassVar[str] = "book_imbalance"
    requires: ClassVar[frozenset[str]] = frozenset()

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        # Fetch recent order book snapshots (last 2 minutes)
        rows = await ctx.conn.fetch(
            """
            SELECT bids_json, asks_json
            FROM order_book_snapshots
            WHERE market_id = $1
              AND ts > NOW() - INTERVAL '2 minutes'
            ORDER BY ts DESC
            LIMIT 3
            """,
            ctx.market_id,
        )
        if not rows:
            return None

        # Aggregate depth across recent snapshots
        total_bid_depth = 0.0
        total_ask_depth = 0.0
        count = 0

        for row in rows:
            bids = row["bids_json"]
            asks = row["asks_json"]
            if not bids or not asks:
                continue
            if isinstance(bids, str):
                import json
                bids = json.loads(bids)
                asks = json.loads(asks)

            bid_depth = sum(float(b.get("size", 0)) for b in bids if b)
            ask_depth = sum(float(a.get("size", 0)) for a in asks if a)
            total_bid_depth += bid_depth
            total_ask_depth += ask_depth
            count += 1

        if count == 0:
            return None

        avg_bid = total_bid_depth / count
        avg_ask = total_ask_depth / count
        total = avg_bid + avg_ask

        if total < _MIN_TOTAL_DEPTH:
            return None

        # Calculate imbalance ratio [-1, 1]
        imbalance = (avg_bid - avg_ask) / total

        if abs(imbalance) < _IMBALANCE_THRESHOLD:
            return None

        # Predict short-term directional pressure.
        # Scale shift by distance from extremes — prices near 0 or 1 move less.
        elasticity = 4.0 * ctx.mid * (1.0 - ctx.mid)  # peaks at 0.5, zero at 0/1
        shift = imbalance * _PROB_SHIFT_SCALE * elasticity
        model_prob = max(0.01, min(0.99, ctx.mid + shift))
        market_prob = ctx.mid
        edge = (model_prob - market_prob) * 100.0

        if abs(edge) < settings.min_edge_cents:
            return None

        # Confidence based on strength of imbalance and depth
        confidence = min(0.85, 0.5 + abs(imbalance) * 0.5)

        direction = "bullish" if imbalance > 0 else "bearish"
        reasoning = (
            f"Order book imbalance {direction}: "
            f"bid_depth={avg_bid:.0f}, ask_depth={avg_ask:.0f}, "
            f"ratio={imbalance:+.2f}. "
            f"Suggests price pressure {'upward' if imbalance > 0 else 'downward'}."
        )

        return StrategyResult(
            model_prob=model_prob,
            market_prob=market_prob,
            edge_cents=edge,
            confidence=confidence,
            reasoning=reasoning,
            signal_json={
                "imbalance": round(imbalance, 3),
                "bid_depth": round(avg_bid, 1),
                "ask_depth": round(avg_ask, 1),
                "snapshots_used": count,
            },
        )
