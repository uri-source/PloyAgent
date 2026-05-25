from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.client import WebSocketClientProtocol

from ploy_agent.common.config import settings
from ploy_agent.common.logging_config import get_logger
from ploy_agent.common.ssl_utils import websocket_ssl_context

from .book_math import best_bid_ask_from_book, depth_within_one_cent, mid_from_ba

log = get_logger("ingestion.ws")


class LocalBook:
    """In-memory L2 book per asset_id (string)."""

    def __init__(self) -> None:
        self.bids: dict[str, dict[float, float]] = defaultdict(dict)
        self.asks: dict[str, dict[float, float]] = defaultdict(dict)

    def apply_full_book(self, asset_id: str, bids: list[dict], asks: list[dict]) -> None:
        self.bids[asset_id].clear()
        self.asks[asset_id].clear()
        for b in bids:
            raw_p, raw_s = b.get("price"), b.get("size")
            if raw_p is None or raw_s is None:
                continue
            try:
                p, s = float(str(raw_p)), float(str(raw_s))
            except (ValueError, TypeError):
                continue
            if s > 0:
                self.bids[asset_id][p] = s
        for a in asks:
            raw_p, raw_s = a.get("price"), a.get("size")
            if raw_p is None or raw_s is None:
                continue
            try:
                p, s = float(str(raw_p)), float(str(raw_s))
            except (ValueError, TypeError):
                continue
            if s > 0:
                self.asks[asset_id][p] = s

    def apply_price_change(self, asset_id: str, price: str, size: str, side: str) -> None:
        p = float(price)
        s = float(size)
        book_side = self.bids if side == "BUY" else self.asks
        if s == 0:
            book_side[asset_id].pop(p, None)
        else:
            book_side[asset_id][p] = s

    def levels(self, asset_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        bids = [{"price": str(p), "size": str(s)} for p, s in sorted(self.bids[asset_id].items(), reverse=True)]
        asks = [{"price": str(p), "size": str(s)} for p, s in sorted(self.asks[asset_id].items())]
        return bids, asks


async def run_market_ws(
    asset_ids: list[str],
    asset_to_market: dict[str, str],
    on_update: Callable[[str, float | None, float | None, float | None, float | None], Awaitable[None]],
    on_trade_snapshot: Callable[[str, list[dict], list[dict]], Awaitable[None]],
    stop: asyncio.Event,
) -> None:
    backoff = 1.0
    max_backoff = 60.0
    while not stop.is_set():
        try:
            ws_kw: dict[str, Any] = {
                "ping_interval": 20,
                "ping_timeout": 120,
                "max_size": 10_000_000,
            }
            ws_kw["ssl"] = websocket_ssl_context()
            async with websockets.connect(settings.poly_ws_url, **ws_kw) as ws:
                log.info("ws_connected", url=settings.poly_ws_url)
                backoff = 1.0
                await _subscribe(ws, asset_ids)
                book = LocalBook()
                async for raw in ws:
                    if stop.is_set():
                        break
                    msg = json.loads(raw)
                    et = msg.get("event_type")
                    if et == "book":
                        await _handle_book(msg, book, asset_to_market, on_update, on_trade_snapshot)
                    elif et == "price_change":
                        await _handle_price_change(msg, book, asset_to_market, on_update)
                    elif et == "best_bid_ask":
                        await _handle_best_bid_ask(msg, book, asset_to_market, on_update)
                    elif et == "last_trade_price":
                        aid = str(msg.get("asset_id"))
                        mid = asset_to_market.get(aid)
                        if not mid:
                            continue
                        bids, asks = book.levels(aid)
                        await on_trade_snapshot(mid, bids, asks)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("ws_error", error=str(e), backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(max_backoff, backoff * 2)


async def _subscribe(ws: WebSocketClientProtocol, asset_ids: list[str]) -> None:
    # Chunk to avoid oversized payloads
    chunk_size = 80
    for i in range(0, len(asset_ids), chunk_size):
        chunk = asset_ids[i : i + chunk_size]
        payload = {
            "assets_ids": chunk,
            "type": "market",
            "custom_feature_enabled": True,
        }
        await ws.send(json.dumps(payload))
        log.info("ws_subscribed", n=len(chunk))


async def _handle_book(
    msg: dict[str, Any],
    book: LocalBook,
    asset_to_market: dict[str, str],
    on_update: Callable[..., Awaitable[None]],
    on_trade_snapshot: Callable[..., Awaitable[None]],
) -> None:
    aid = str(msg.get("asset_id"))
    mid = asset_to_market.get(aid)
    if not mid:
        return
    bids = msg.get("bids") or []
    asks = msg.get("asks") or []
    book.apply_full_book(aid, bids, asks)
    bb, ba = best_bid_ask_from_book(bids, asks)
    m = mid_from_ba(bb, ba)
    depth = depth_within_one_cent(bids, asks, bb, ba)
    await on_update(mid, bb, ba, m, depth)


async def _handle_price_change(
    msg: dict[str, Any],
    book: LocalBook,
    asset_to_market: dict[str, str],
    on_update: Callable[..., Awaitable[None]],
) -> None:
    for ch in msg.get("price_changes") or []:
        aid = str(ch.get("asset_id"))
        market_id = asset_to_market.get(aid)
        if not market_id:
            continue
        raw_p, raw_s, raw_side = ch.get("price"), ch.get("size"), ch.get("side")
        if raw_p is None or raw_s is None or raw_side is None:
            continue
        try:
            book.apply_price_change(aid, str(raw_p), str(raw_s), str(raw_side))
        except (ValueError, TypeError):
            continue
        bids, asks = book.levels(aid)
        bb = float(ch["best_bid"]) if ch.get("best_bid") not in (None, "") else None
        ba = float(ch["best_ask"]) if ch.get("best_ask") not in (None, "") else None
        if bb == 0:
            bb = None
        if ba == 0:
            ba = None
        m = mid_from_ba(bb, ba)
        depth = depth_within_one_cent(bids, asks, bb, ba)
        await on_update(market_id, bb, ba, m, depth)


async def _handle_best_bid_ask(
    msg: dict[str, Any],
    book: LocalBook,
    asset_to_market: dict[str, str],
    on_update: Callable[..., Awaitable[None]],
) -> None:
    aid = str(msg.get("asset_id"))
    market_id = asset_to_market.get(aid)
    if not market_id:
        return
    bb = float(msg["best_bid"]) if msg.get("best_bid") not in (None, "") else None
    ba = float(msg["best_ask"]) if msg.get("best_ask") not in (None, "") else None
    if bb == 0:
        bb = None
    if ba == 0:
        ba = None
    m = mid_from_ba(bb, ba)
    # Use real book depth if available, else 0 (no fake pseudo-depth)
    bids, asks = book.levels(aid)
    depth = depth_within_one_cent(bids, asks, bb, ba)
    await on_update(market_id, bb, ba, m, depth)
