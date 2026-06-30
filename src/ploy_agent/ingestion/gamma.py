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


def _bundles_from_events(events: list[Any], seen: set[str]) -> list[dict[str, Any]]:
    markets_out: list[dict[str, Any]] = []
    for ev in events:
        for m in ev.get("markets") or []:
            mid = str(m.get("id") or "")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            markets_out.append({"event": ev, "market": m})
    return markets_out


async def _fetch_event_bundles(
    client: httpx.AsyncClient,
    params: dict[str, Any],
    seen: set[str],
) -> list[dict[str, Any]]:
    base = settings.gamma_base_url.rstrip("/")
    r = await client.get(f"{base}/events", params=params, timeout=60.0)
    r.raise_for_status()
    events = r.json()
    if not isinstance(events, list):
        return []
    return _bundles_from_events(events, seen)


async def discover_markets_by_event_slugs(
    client: httpx.AsyncClient, slugs: list[str] | None = None
) -> list[dict[str, Any]]:
    """Pull active markets for explicit Gamma event slugs (IPO, politics, etc.)."""
    slug_list = slugs if slugs is not None else settings.discovery_event_slug_list()
    markets_out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for slug in slug_list:
        markets_out.extend(
            await _fetch_event_bundles(
                client,
                {"slug": slug, "active": "true"},
                seen,
            )
        )
    return markets_out


async def discover_markets_by_series_slugs(
    client: httpx.AsyncClient, series_slugs: list[str] | None = None
) -> list[dict[str, Any]]:
    """Pull active markets for all events in Gamma series (e.g. soccer-fifwc)."""
    slug_list = series_slugs if series_slugs is not None else settings.discovery_series_slug_list()
    markets_out: list[dict[str, Any]] = []
    seen: set[str] = set()
    limit = max(1, settings.poly_gamma_discovery_limit)

    for slug in slug_list:
        markets_out.extend(
            await _fetch_event_bundles(
                client,
                {
                    "series_slug": slug,
                    "closed": "false",
                    "active": "true",
                    "limit": limit,
                },
                seen,
            )
        )
    return markets_out


def merge_market_bundles(
    *bundle_lists: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate discovery results by Polymarket market id."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bundles in bundle_lists:
        for b in bundles:
            mid = str((b.get("market") or {}).get("id") or "")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            out.append(b)
    return out


async def discover_markets(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Tag-, series-, and pinned-slug discovery."""
    by_tags = await discover_markets_by_tags(client)
    by_series = await discover_markets_by_series_slugs(client)
    by_slugs = await discover_markets_by_event_slugs(client)
    return merge_market_bundles(by_tags, by_series, by_slugs)


async def discover_markets_by_tags(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """
    Pull active markets from Gamma for each configured tag.
    Uses POLY_GAMMA_TAGS, or POLY_NBA_TAGS if POLY_GAMMA_TAGS is empty
    (comma-separated slugs or numeric ids).
    """
    tags = [t.strip() for t in settings.discovery_tag_csv().split(",") if t.strip()]
    markets_out: list[dict[str, Any]] = []
    seen: set[str] = set()
    limit = max(1, settings.poly_gamma_discovery_limit)

    for tag in tags:
        params: dict[str, Any] = {
            "closed": "false",
            "active": "true",
            "limit": limit,
        }
        if tag.isdigit():
            params["tag_id"] = int(tag)
        else:
            params["tag_slug"] = tag
        markets_out.extend(await _fetch_event_bundles(client, params, seen))
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
