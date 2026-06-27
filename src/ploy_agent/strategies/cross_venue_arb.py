from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar

from ploy_agent.common.config import settings
from ploy_agent.common.cross_venue import (
    edge_cents_from_venues,
    spread_cents,
    spread_widening,
)
from ploy_agent.common.explain import direction_label
from ploy_agent.kalshi import repo as krepo
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.types import StrategyContext, StrategyResult


def _cross_venue_confidence(
    *,
    edge_cents: float,
    poly_mid: float,
    kalshi_mid: float,
    outcome_map: str,
    poly_depth: float,
    poly_spread: float | None,
    kalshi_depth: float,
    poly_age_sec: float,
    kalshi_age_sec: float,
    hours_to_resolution: float | None,
    spread_samples: list[float],
) -> tuple[float, str]:
    from ploy_agent.common.confidence import statistical_confidence

    raw_gap = abs(spread_cents(poly_mid, kalshi_mid, outcome_map=outcome_map)) / 100.0
    base, _ = statistical_confidence(
        depth_1c=min(poly_depth, kalshi_depth),
        spread=poly_spread,
        n_siblings=1,
        sum_deviation=raw_gap,
        edge_cents=edge_cents,
        mid=poly_mid,
        is_binary_pair=True,
    )
    conf = base
    reasons: list[str] = []

    max_stale = settings.cross_venue_max_stale_sec
    if poly_age_sec > max_stale or kalshi_age_sec > max_stale:
        conf *= 0.4
        reasons.append(f"stale feed (poly {int(poly_age_sec)}s, kalshi {int(kalshi_age_sec)}s)")
    elif max(poly_age_sec, kalshi_age_sec) > max_stale / 2:
        conf *= 0.75
        reasons.append("one feed lagging")

    if min(poly_depth, kalshi_depth) < settings.cross_venue_min_depth:
        conf *= 0.5
        reasons.append("thin cross-venue liquidity")

    if hours_to_resolution is not None and hours_to_resolution > 168:
        conf *= 0.6
        reasons.append("resolution >7d out")

    if spread_widening(spread_samples):
        conf *= 0.5
        reasons.append("spread widening (5m)")

    conf = max(0.15, min(0.95, conf))
    detail = "; ".join(reasons) if reasons else "feeds synced, spread stable"
    return round(conf, 3), f"Cross-venue confidence: {detail}. Score: {conf:.2f}."


class CrossVenueArbStrategy(Strategy):
    id: ClassVar[str] = "cross_venue_arb"
    requires: ClassVar[frozenset[str]] = frozenset({"kalshi"})

    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        if not settings.kalshi_enabled:
            return None

        pair = await krepo.pair_for_poly_market(ctx.conn, ctx.market_id)
        if pair is None:
            return None
        if not pair["resolution_aligned"]:
            return None

        outcome_map = str(pair["outcome_map"])
        ticker = str(pair["kalshi_ticker"])
        krow = await krepo.latest_kalshi_price(ctx.conn, ticker)
        if krow is None or krow["mid"] is None:
            return None

        kalshi_mid = float(krow["mid"])
        poly_mid = float(ctx.mid)
        model_prob, edge = edge_cents_from_venues(
            poly_mid,
            kalshi_mid,
            outcome_map=outcome_map,
            poly_fee_rate=settings.poly_fee_rate,
            kalshi_fee_rate=settings.kalshi_fee_rate,
        )

        if abs(edge) < settings.cross_venue_min_edge_cents:
            return None

        now = krow["ts"]
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        def _age_sec(ts: datetime | None) -> float:
            if ts is None:
                return 9999.0
            ref = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            return max(0.0, (now - ref).total_seconds())

        price_row = await ctx.conn.fetchrow(
            """
            SELECT ts, depth_1c, bid, ask FROM prices
            WHERE market_id = $1 AND mid IS NOT NULL
            ORDER BY ts DESC LIMIT 1
            """,
            ctx.market_id,
        )
        poly_age = 9999.0
        poly_depth = ctx.depth_1c
        poly_spread = ctx.spread
        if price_row:
            poly_age = _age_sec(price_row["ts"])
            poly_depth = float(price_row["depth_1c"] or 0)
            if price_row["bid"] is not None and price_row["ask"] is not None:
                poly_spread = float(price_row["ask"]) - float(price_row["bid"])

        kalshi_age = _age_sec(krow["ts"])
        kalshi_depth = float(krow["depth_1c"] or 0)

        if poly_age > settings.cross_venue_max_stale_sec or kalshi_age > settings.cross_venue_max_stale_sec:
            return None
        if min(poly_depth, kalshi_depth) < settings.cross_venue_min_depth:
            return None

        end_date = ctx.mrow.get("end_date")
        hours_to_res: float | None = None
        if end_date is not None:
            ed = end_date
            if ed.tzinfo is None:
                ed = ed.replace(tzinfo=timezone.utc)
            hours_to_res = max(0.0, (ed - now).total_seconds() / 3600.0)

        spread_samples = await krepo.recent_spread_samples(
            ctx.conn,
            poly_market_id=ctx.market_id,
            kalshi_ticker=ticker,
            outcome_map=outcome_map,
        )

        conf, conf_reason = _cross_venue_confidence(
            edge_cents=edge,
            poly_mid=poly_mid,
            kalshi_mid=kalshi_mid,
            outcome_map=outcome_map,
            poly_depth=poly_depth,
            poly_spread=poly_spread,
            kalshi_depth=kalshi_depth,
            poly_age_sec=poly_age,
            kalshi_age_sec=kalshi_age,
            hours_to_resolution=hours_to_res,
            spread_samples=spread_samples,
        )

        gap = spread_cents(poly_mid, kalshi_mid, outcome_map=outcome_map)
        label = str(pair["label"])
        reasoning = (
            f"{direction_label(edge)}: “{label}” — Polymarket {poly_mid:.2f} vs Kalshi "
            f"{kalshi_mid:.2f} ({outcome_map} map), raw gap {gap:+.1f}¢, "
            f"fee-adj edge {abs(edge):.1f}¢; poly feed {int(poly_age)}s, kalshi {int(kalshi_age)}s."
        )

        return StrategyResult(
            model_prob=model_prob,
            market_prob=poly_mid,
            edge_cents=edge,
            confidence=conf,
            reasoning=reasoning,
            sources=[
                {"type": "kalshi", "detail": ticker},
                {"type": "polymarket", "detail": ctx.market_id},
            ],
            signal_json={
                "pair_id": pair["id"],
                "kalshi_ticker": ticker,
                "kalshi_mid": kalshi_mid,
                "poly_mid": poly_mid,
                "outcome_map": outcome_map,
                "raw_gap_cents": gap,
                "poly_age_sec": poly_age,
                "kalshi_age_sec": kalshi_age,
                "spread_samples": spread_samples[-10:],
                "confidence_detail": conf_reason,
            },
        )
