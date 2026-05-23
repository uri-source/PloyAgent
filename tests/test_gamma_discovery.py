from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ploy_agent.ingestion.gamma import (
    discover_markets_by_event_slugs,
    merge_market_bundles,
)


@pytest.mark.asyncio
async def test_discover_markets_by_event_slugs() -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = [
        {
            "id": "ev1",
            "slug": "spacex-or-openai-higher-ipo-closing-market-cap",
            "markets": [
                {
                    "id": "m1",
                    "clobTokenIds": '["yes1","no1"]',
                    "active": True,
                }
            ],
        }
    ]
    client = AsyncMock()
    client.get.return_value = response
    bundles = await discover_markets_by_event_slugs(
        client, slugs=["spacex-or-openai-higher-ipo-closing-market-cap"]
    )
    assert len(bundles) == 1
    assert bundles[0]["market"]["id"] == "m1"


def test_merge_market_bundles_dedupes() -> None:
    b1 = [{"market": {"id": "a"}}, {"market": {"id": "b"}}]
    b2 = [{"market": {"id": "b"}}, {"market": {"id": "c"}}]
    merged = merge_market_bundles(b1, b2)
    assert [x["market"]["id"] for x in merged] == ["a", "b", "c"]
