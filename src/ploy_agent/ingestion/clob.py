from __future__ import annotations

from typing import Any

import httpx

from ploy_agent.common.config import settings

from .book_math import best_bid_ask_from_book, depth_within_one_cent, mid_from_ba


async def fetch_order_book(client: httpx.AsyncClient, token_id: str) -> tuple[list[Any], list[Any]]:
    url = f"{settings.clob_base_url.rstrip('/')}/book"
    r = await client.get(url, params={"token_id": token_id}, timeout=30.0)
    r.raise_for_status()
    data = r.json()
    bids = data.get("bids") or []
    asks = data.get("asks") or []
    return bids, asks


async def reconcile_book(
    client: httpx.AsyncClient, token_id: str
) -> tuple[float | None, float | None, float | None, float | None, list[Any], list[Any]]:
    bids, asks = await fetch_order_book(client, token_id)
    bb, ba = best_bid_ask_from_book(bids, asks)
    mid = mid_from_ba(bb, ba)
    depth = depth_within_one_cent(bids, asks, bb, ba)
    return bb, ba, mid, depth, bids, asks
