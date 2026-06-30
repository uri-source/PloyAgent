from __future__ import annotations

import json
from typing import ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.logging_config import get_logger
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult

log = get_logger("strategies.book_imbalance")

# Order book imbalance: bid/ask depth asymmetry → directional pressure signal.

# Minimum imbalance ratio to consider a signal (SELL side)
_IMBALANCE_THRESHOLD = 0.35
# BUY signals require 2x threshold (retail bid pressure at high prices is noisy)
_BUY_IMBALANCE_MULTIPLIER = 2.0
# How much to shift probability based on imbalance magnitude
_PROB_SHIFT_SCALE = 0.08
# Minimum total depth to trust the signal
_MIN_TOTAL_DEPTH = 500.0
# Require same-sign imbalance in at least 2 of last 3 snapshots
_MIN_SUSTAINED_SNAPSHOTS = 2
_SELL_CONFIDENCE_FLOOR = 0.60
_BUY_CONFIDENCE_FLOOR = 0.70


def _snapshot_imbalance(bids: object, asks: object) -> tuple[float, float, float] | None:
    if not bids or not asks:
        return None
    if isinstance(bids, str):
        bids = json.loads(bids)
        asks = json.loads(asks)
    if not isinstance(bids, list) or not isinstance(asks, list):
        return None
    bid_depth = sum(float(b.get("size", 0)) for b in bids if b)
    ask_depth = sum(float(a.get("size", 0)) for a in asks if a)
    total = bid_depth + ask_depth
    if total < _MIN_TOTAL_DEPTH:
        return None
    return bid_depth, ask_depth, (bid_depth - ask_depth) / total


def _sustained_same_sign(ratios: list[float]) -> bool:
    if len(ratios) < _MIN_SUSTAINED_SNAPSHOTS:
        return False
    pos = sum(1 for r in ratios if r > 0)
    neg = sum(1 for r in ratios if r < 0)
    return pos >= _MIN_SUSTAINED_SNAPSHOTS or neg >= _MIN_SUSTAINED_SNAPSHOTS


class BookImbalanceStrategy(Strategy):
    id: ClassVar[str] = "book_imbalance"
    requires: ClassVar[frozenset[str]] = frozenset()

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
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

        per_snap: list[tuple[float, float, float]] = []
        for row in rows:
            parsed = _snapshot_imbalance(row["bids_json"], row["asks_json"])
            if parsed:
                per_snap.append(parsed)

        if len(per_snap) < _MIN_SUSTAINED_SNAPSHOTS:
            return None

        ratios = [x[2] for x in per_snap]
        if not _sustained_same_sign(ratios):
            return None

        avg_bid = sum(x[0] for x in per_snap) / len(per_snap)
        avg_ask = sum(x[1] for x in per_snap) / len(per_snap)
        imbalance = sum(ratios) / len(ratios)

        threshold = _IMBALANCE_THRESHOLD
        if imbalance > 0:
            threshold *= _BUY_IMBALANCE_MULTIPLIER

        if abs(imbalance) < threshold:
            return None

        elasticity = 4.0 * ctx.mid * (1.0 - ctx.mid)
        shift = imbalance * _PROB_SHIFT_SCALE * elasticity
        model_prob = max(0.01, min(0.99, ctx.mid + shift))
        market_prob = ctx.mid
        edge = (model_prob - market_prob) * 100.0

        if abs(edge) < settings.min_edge_cents:
            return None

        confidence = min(0.85, 0.5 + abs(imbalance) * 0.5)
        if edge > 0:
            confidence = max(confidence, _BUY_CONFIDENCE_FLOOR)
        else:
            confidence = max(confidence, _SELL_CONFIDENCE_FLOOR)

        direction = "bullish" if imbalance > 0 else "bearish"
        reasoning = (
            f"Order book imbalance {direction} (sustained {len(per_snap)}/3 snaps): "
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
                "snapshots_used": len(per_snap),
                "sustained": True,
            },
        )
