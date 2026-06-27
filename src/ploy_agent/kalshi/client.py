from __future__ import annotations

from typing import Any

import httpx

from ploy_agent.common.config import settings
from ploy_agent.common.logging_config import get_logger
from ploy_agent.common.ssl_utils import httpx_verify

log = get_logger("kalshi.client")


def _base() -> str:
    return settings.kalshi_base_url.rstrip("/")


def parse_orderbook(payload: dict[str, Any]) -> tuple[float | None, float | None, float | None, float]:
    """Return bid, ask, mid, depth_1c from Kalshi orderbook JSON."""
    ob = payload.get("orderbook_fp") or payload.get("orderbook") or payload
    yes_raw = ob.get("yes_dollars") or ob.get("yes") or []
    no_raw = ob.get("no_dollars") or ob.get("no") or []

    def _to_prob_qty(row: Any) -> tuple[float, float]:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            p, q = float(row[0]), float(row[1])
            if p > 1.0:
                p /= 100.0
            return p, q
        return 0.0, 0.0

    yes_levels = [_to_prob_qty(r) for r in yes_raw if r]
    no_levels = [_to_prob_qty(r) for r in no_raw if r]
    yes_levels.sort(key=lambda x: x[0], reverse=True)
    no_levels.sort(key=lambda x: x[0], reverse=True)

    yes_bid = yes_levels[0][0] if yes_levels else None
    yes_ask = (1.0 - no_levels[0][0]) if no_levels else None
    if yes_bid is not None and yes_ask is not None:
        mid = (yes_bid + yes_ask) / 2.0
    elif yes_bid is not None:
        mid = yes_bid
    elif yes_ask is not None:
        mid = yes_ask
    else:
        mid = None

    depth = 0.0
    if yes_levels and yes_bid is not None:
        for price, qty in yes_levels:
            if yes_bid - price <= 0.01:
                depth += price * qty
    elif no_levels and yes_ask is not None:
        for price, qty in no_levels:
            implied_yes = 1.0 - price
            if yes_ask - implied_yes <= 0.01:
                depth += implied_yes * qty

    return yes_bid, yes_ask, mid, depth


async def fetch_market(http: httpx.AsyncClient, ticker: str) -> dict[str, Any] | None:
    url = f"{_base()}/markets/{ticker}"
    try:
        r = await http.get(url, timeout=20.0)
        r.raise_for_status()
        data = r.json()
        return data.get("market") or data
    except Exception as e:
        log.warning("kalshi_market_fetch_failed", ticker=ticker, error=str(e))
        return None


async def fetch_orderbook(http: httpx.AsyncClient, ticker: str) -> dict[str, Any] | None:
    url = f"{_base()}/markets/{ticker}/orderbook"
    try:
        r = await http.get(url, timeout=20.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("kalshi_orderbook_fetch_failed", ticker=ticker, error=str(e))
        return None


def new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=httpx_verify(), headers={"Accept": "application/json"})
