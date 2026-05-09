from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import asyncpg

from ploy_agent.common.scoring import composite_score, hours_until


@dataclass
class RankedPick:
    market_id: str
    strategy_id: str
    question: str | None
    end_date: datetime | None
    mid: float
    model_prob: float
    market_prob: float
    edge_cents: float
    confidence: float
    reasoning: str
    depth_1c: float
    score: float


async def top_picks(
    conn: asyncpg.Connection,
    limit: int = 5,
    *,
    strategy_ids: list[str] | None = None,
    merge_by_market: bool = False,
) -> list[RankedPick]:
    sids = strategy_ids
    if not sids:
        rows_ids = await conn.fetch("SELECT DISTINCT strategy_id FROM fair_values")
        sids = [str(r["strategy_id"]) for r in rows_ids]
    if not sids:
        return []

    rows = await conn.fetch(
        """
        WITH lp AS (
          SELECT DISTINCT ON (market_id) market_id, mid, depth_1c, ts
          FROM prices
          WHERE mid IS NOT NULL
          ORDER BY market_id, ts DESC
        ),
        lf AS (
          SELECT DISTINCT ON (market_id, strategy_id)
            market_id, strategy_id, model_prob, market_prob, edge_cents, confidence, reasoning, ts
          FROM fair_values
          WHERE strategy_id = ANY($1::text[])
          ORDER BY market_id, strategy_id, ts DESC
        )
        SELECT m.id AS market_id,
               lf.strategy_id,
               m.question,
               m.end_date,
               lp.mid,
               lf.model_prob,
               lf.market_prob,
               lf.edge_cents,
               lf.confidence,
               lf.reasoning,
               COALESCE(lp.depth_1c, 0.0) AS depth_1c
        FROM markets m
        JOIN lp ON lp.market_id = m.id
        JOIN lf ON lf.market_id = m.id
        WHERE m.status IS DISTINCT FROM 'closed'
        """,
        sids,
    )
    picks: list[RankedPick] = []
    for r in rows:
        end = r["end_date"]
        if isinstance(end, datetime) and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        h = hours_until(end)
        edge = float(r["edge_cents"])
        conf = float(r["confidence"])
        depth = float(r["depth_1c"] or 0.0)
        sc = composite_score(edge, depth, conf, h)
        picks.append(
            RankedPick(
                market_id=str(r["market_id"]),
                strategy_id=str(r["strategy_id"]),
                question=r.get("question"),
                end_date=r["end_date"],
                mid=float(r["mid"]),
                model_prob=float(r["model_prob"]),
                market_prob=float(r["market_prob"]),
                edge_cents=edge,
                confidence=conf,
                reasoning=str(r.get("reasoning") or ""),
                depth_1c=depth,
                score=sc,
            )
        )
    picks.sort(key=lambda p: p.score, reverse=True)
    if merge_by_market:
        best: dict[str, RankedPick] = {}
        for p in picks:
            cur = best.get(p.market_id)
            if cur is None or p.score > cur.score:
                best[p.market_id] = p
        picks = sorted(best.values(), key=lambda p: p.score, reverse=True)
    return picks[:limit]
