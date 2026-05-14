from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ploy_agent.common.config import settings


def _parse_dt(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(float(val) / 1000.0, tz=timezone.utc)
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


async def fetch_sports_tags(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    r = await client.get(f"{settings.gamma_base_url.rstrip('/')}/sports", timeout=30.0)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


async def discover_markets_by_tags(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """
    Pull active markets from Gamma for each configured tag.
    Uses POLY_GAMMA_TAGS, or POLY_NBA_TAGS if POLY_GAMMA_TAGS is empty
    (comma-separated slugs or numeric ids).
    """
    base = settings.gamma_base_url.rstrip("/")
    tags = [t.strip() for t in settings.discovery_tag_csv().split(",") if t.strip()]
    markets_out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for tag in tags:
        params: dict[str, Any] = {
            "closed": "false",
            "active": "true",
            "limit": 200,
        }
        if tag.isdigit():
            params["tag_id"] = int(tag)
        else:
            params["tag_slug"] = tag
        r = await client.get(f"{base}/events", params=params, timeout=60.0)
        r.raise_for_status()
        events = r.json()
        if not isinstance(events, list):
            continue
        for ev in events:
            for m in ev.get("markets") or []:
                mid = str(m.get("id") or "")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                markets_out.append({"event": ev, "market": m})
    return markets_out


def normalize_market_row(bundle: dict[str, Any]) -> dict[str, Any] | None:
    m = bundle["market"]
    ev = bundle["event"]
    mid = str(m.get("id") or "")
    tokens = m.get("clobTokenIds") or m.get("clob_token_ids")
    if isinstance(tokens, str):
        try:
            import json as _json

            tokens = _json.loads(tokens)
        except Exception:
            tokens = None
    if not mid or not tokens or len(tokens) < 1:
        return None
    yes = str(tokens[0])
    no = str(tokens[1]) if len(tokens) > 1 else None
    end = _parse_dt(m.get("endDate") or m.get("endDateIso") or ev.get("endDate"))
    tags = ev.get("tags")
    if isinstance(tags, list) and tags:
        if isinstance(tags[0], dict):
            cat = str(tags[0].get("slug") or tags[0].get("label") or "nba")
        else:
            cat = str(tags[0])
    else:
        cat = "nba"
    ev_id = str(ev.get("id") or "") or None
    ev_slug = str(ev.get("slug") or "") or None
    return {
        "market_id": mid,
        "slug": m.get("slug") or ev.get("slug"),
        "question": m.get("question") or ev.get("title"),
        "resolution_criteria": m.get("description") or ev.get("description"),
        "end_date": end,
        "category": cat,
        "status": "active" if m.get("active", True) else "closed",
        "condition_id": str(m.get("conditionId") or m.get("condition_id") or ""),
        "clob_asset_id": yes,
        "companion_clob_asset_id": no,
        "gamma_event_id": ev_id,
        "event_slug": ev_slug,
    }
