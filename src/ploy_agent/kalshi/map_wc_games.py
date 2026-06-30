from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ploy_agent.common.config import settings
from ploy_agent.common.logging_config import get_logger
from ploy_agent.kalshi import client
from ploy_agent.kalshi import repo as krepo
from ploy_agent.kalshi.wc_game_mapper import load_team_aliases, match_wc_game_pairs

log = get_logger("kalshi.map_wc_games")


@dataclass
class MapStats:
    matched_active: int = 0
    matched_review: int = 0
    skipped: int = 0
    kalshi_markets: int = 0
    poly_candidates: int = 0


async def map_wc_games(
    conn: Any,
    http: httpx.AsyncClient,
    *,
    dry_run: bool = False,
    min_confidence: float | None = None,
    review_confidence: float | None = None,
) -> MapStats:
    min_conf = (
        settings.cross_venue_map_min_confidence
        if min_confidence is None
        else min_confidence
    )
    review_conf = (
        settings.cross_venue_map_review_confidence
        if review_confidence is None
        else review_confidence
    )
    series = settings.kalshi_wc_game_series
    prefix = settings.cross_venue_poly_event_slug_prefix

    aliases = load_team_aliases()
    kalshi_markets = await client.list_markets(http, series_ticker=series, status="open")
    poly_rows = await conn.fetch(
        """
        SELECT id, question, event_slug, end_date, status
        FROM markets
        WHERE status IS DISTINCT FROM 'closed'
          AND event_slug LIKE $1 || '%'
        """,
        prefix,
    )

    stats = MapStats(
        kalshi_markets=len(kalshi_markets),
        poly_candidates=len(poly_rows),
    )
    pairs = match_wc_game_pairs(
        [dict(r) for r in poly_rows],
        kalshi_markets,
        aliases,
        min_confidence=min_conf,
        review_confidence=review_conf,
    )

    for mp in pairs:
        if mp.confidence < review_conf:
            stats.skipped += 1
            continue
        if mp.active:
            stats.matched_active += 1
        else:
            stats.matched_review += 1

        if dry_run:
            log.info(
                "wc_pair_candidate",
                pair_id=mp.pair_id,
                confidence=mp.confidence,
                active=mp.active,
                poly=mp.poly_market_id,
                kalshi=mp.kalshi_ticker,
            )
            continue

        km = next((m for m in kalshi_markets if str(m.get("ticker")) == mp.kalshi_ticker), None)
        if km:
            from datetime import datetime

            close_time = None
            ct = km.get("close_time") or km.get("expiration_time")
            if ct:
                try:
                    close_time = datetime.fromisoformat(str(ct).replace("Z", "+00:00"))
                except ValueError:
                    close_time = None
            await krepo.upsert_kalshi_market(
                conn,
                ticker=mp.kalshi_ticker,
                title=km.get("title"),
                status=km.get("status"),
                series_ticker=km.get("series_ticker") or series,
                close_time=close_time,
            )

        await krepo.upsert_pair(
            conn,
            pair_id=mp.pair_id,
            label=mp.label,
            poly_market_id=mp.poly_market_id,
            kalshi_ticker=mp.kalshi_ticker,
            outcome_map="same",
            resolution_aligned=True,
            notes=mp.review_notes,
            active=mp.active,
            match_confidence=mp.confidence,
            match_source="auto_wc_game",
            kalshi_event_ticker=mp.kalshi_event_ticker,
            poly_event_slug=mp.poly_event_slug,
            review_notes=mp.review_notes,
        )

    return stats
