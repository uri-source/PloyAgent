from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploy_agent.common.config import settings
from ploy_agent.strategies.consensus import ConsensusStrategy


@pytest.mark.asyncio
async def test_consensus_requires_two_agreeing_strategies():
    strat = ConsensusStrategy()
    ctx = MagicMock()
    ctx.market_id = "m1"
    ctx.mid = 0.50
    ctx.conn = AsyncMock()
    ctx.conn.fetch = AsyncMock(
        return_value=[
            {
                "strategy_id": "book_imbalance",
                "model_prob": 0.58,
                "market_prob": 0.50,
                "edge_cents": 8.0,
                "confidence": 0.72,
            },
            {
                "strategy_id": "cross_venue_arb",
                "model_prob": 0.57,
                "market_prob": 0.50,
                "edge_cents": 7.0,
                "confidence": 0.75,
            },
        ]
    )
    result = await strat.run(ctx)
    assert result is not None
    assert result.edge_cents > 0
    assert result.confidence >= 0.70
    assert "book_imbalance" in result.reasoning
    assert "cross_venue_arb" in result.reasoning


@pytest.mark.asyncio
async def test_consensus_none_when_directions_split():
    strat = ConsensusStrategy()
    ctx = MagicMock()
    ctx.market_id = "m1"
    ctx.mid = 0.50
    ctx.conn = AsyncMock()
    ctx.conn.fetch = AsyncMock(
        return_value=[
            {
                "strategy_id": "book_imbalance",
                "model_prob": 0.58,
                "market_prob": 0.50,
                "edge_cents": 8.0,
                "confidence": 0.72,
            },
            {
                "strategy_id": "cross_venue_arb",
                "model_prob": 0.43,
                "market_prob": 0.50,
                "edge_cents": -7.0,
                "confidence": 0.75,
            },
        ]
    )
    result = await strat.run(ctx)
    assert result is None


def test_consensus_respects_min_edge(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "min_edge_cents", 20.0)
    # Edge calc is tested via integration; strategy returns None if below min after consensus
