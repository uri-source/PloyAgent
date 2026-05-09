from __future__ import annotations

from typing import Any


def _f(x: Any) -> float:
    try:
        return float(str(x).strip())
    except Exception:
        return 0.0


def best_bid_ask_from_book(
    bids: list[dict[str, Any]], asks: list[dict[str, Any]]
) -> tuple[float | None, float | None]:
    best_bid = max((_f(b.get("price")) for b in bids), default=None)
    best_ask_list = [_f(a.get("price")) for a in asks if _f(a.get("price")) > 0]
    best_ask = min(best_ask_list) if best_ask_list else None
    if best_bid is None and best_ask is None:
        return None, None
    return best_bid, best_ask


def mid_from_ba(best_bid: float | None, best_ask: float | None) -> float | None:
    if best_bid is None and best_ask is None:
        return None
    if best_bid is None:
        return best_ask
    if best_ask is None:
        return best_bid
    return (best_bid + best_ask) / 2.0


def depth_within_one_cent(
    bids: list[dict[str, Any]],
    asks: list[dict[str, Any]],
    best_bid: float | None,
    best_ask: float | None,
    width: float = 0.01,
) -> float:
    """Approximate total size within one cent of touch on both sides."""
    d = 0.0
    if best_bid is not None:
        floor = best_bid - width
        for b in bids:
            p = _f(b.get("price"))
            s = _f(b.get("size"))
            if s > 0 and p >= floor and p <= best_bid:
                d += s
    if best_ask is not None:
        ceiling = best_ask + width
        for a in asks:
            p = _f(a.get("price"))
            s = _f(a.get("size"))
            if s > 0 and p >= best_ask and p <= ceiling:
                d += s
    return d
