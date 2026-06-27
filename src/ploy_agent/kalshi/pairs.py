from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

VALID_OUTCOME_MAPS = frozenset({"same", "inverted"})


@dataclass(frozen=True)
class VenuePairSpec:
    id: str
    label: str
    poly_market_id: str
    kalshi_ticker: str
    outcome_map: str = "same"
    resolution_aligned: bool = True
    notes: str | None = None
    active: bool = True


def load_pairs_yaml(path: Path) -> list[VenuePairSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected mapping at root in {path}")
    items = raw.get("pairs") or []
    if not isinstance(items, list):
        raise ValueError("'pairs' must be a list")
    out: list[VenuePairSpec] = []
    for i, row in enumerate(items):
        if not isinstance(row, dict):
            raise ValueError(f"pairs[{i}] must be a mapping")
        pair_id = str(row.get("id") or "").strip()
        poly_id = str(row.get("poly_market_id") or "").strip()
        ticker = str(row.get("kalshi_ticker") or "").strip()
        if not pair_id or not poly_id or not ticker:
            raise ValueError(f"pairs[{i}] requires id, poly_market_id, kalshi_ticker")
        omap = str(row.get("outcome_map") or "same").strip().lower()
        if omap not in VALID_OUTCOME_MAPS:
            raise ValueError(f"pairs[{i}] outcome_map must be same|inverted")
        out.append(
            VenuePairSpec(
                id=pair_id,
                label=str(row.get("label") or pair_id),
                poly_market_id=poly_id,
                kalshi_ticker=ticker,
                outcome_map=omap,
                resolution_aligned=bool(row.get("resolution_aligned", True)),
                notes=str(row["notes"]) if row.get("notes") else None,
                active=bool(row.get("active", True)),
            )
        )
    return out


async def validate_and_upsert_pairs(
    conn: Any,
    specs: list[VenuePairSpec],
    *,
    strict: bool = False,
) -> dict[str, int]:
    """Upsert pairs; optionally require poly market row + kalshi ticker metadata."""
    from ploy_agent.kalshi import repo as krepo

    loaded = 0
    skipped = 0
    for spec in specs:
        poly_ok = await conn.fetchval(
            "SELECT 1 FROM markets WHERE id = $1",
            spec.poly_market_id,
        )
        kalshi_ok = await conn.fetchval(
            "SELECT 1 FROM kalshi_markets WHERE ticker = $1",
            spec.kalshi_ticker,
        )
        if not poly_ok:
            skipped += 1
            continue
        if strict and not kalshi_ok:
            skipped += 1
            continue
        if not kalshi_ok:
            await krepo.upsert_kalshi_market(
                conn,
                ticker=spec.kalshi_ticker,
                title=spec.label,
                status="pending",
                series_ticker=None,
                close_time=None,
            )
        await krepo.upsert_pair(
            conn,
            pair_id=spec.id,
            label=spec.label,
            poly_market_id=spec.poly_market_id,
            kalshi_ticker=spec.kalshi_ticker,
            outcome_map=spec.outcome_map,
            resolution_aligned=spec.resolution_aligned,
            notes=spec.notes,
            active=spec.active,
        )
        loaded += 1
    return {"loaded": loaded, "skipped": skipped}
