from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.notifier.rank import top_picks

_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_ROOT / "templates"))

_CATALOG_FRESH_SEC = 180.0
_PRICES_FRESH_SEC = 300.0
_GAME_STATE_FRESH_SEC = 180.0
_FAIR_FRESH_SEC = 120.0


async def _pipeline_status(
    conn: Any,
    *,
    n_markets: int,
    n_prices: int,
    n_game_state: int,
    n_fair: int,
) -> list[dict[str, str]]:
    row = await conn.fetchrow("SELECT NOW() AS now")
    now: datetime = row["now"]
    m_up = await conn.fetchval("SELECT MAX(updated_at) FROM markets")
    p_ts = await conn.fetchval("SELECT MAX(ts) FROM prices")
    g_ts = await conn.fetchval("SELECT MAX(ts) FROM game_state")
    f_ts = await conn.fetchval("SELECT MAX(ts) FROM fair_values")

    def age_sec(ts: datetime | None) -> float | None:
        if ts is None:
            return None
        return (now - ts).total_seconds()

    services: list[dict[str, str]] = []

    # Ingest: Gamma/catalog vs quote stream
    cat_age = age_sec(m_up)
    px_age = age_sec(p_ts)
    cat_txt = (
        f"catalog last write {int(cat_age)}s ago"
        if cat_age is not None
        else "no catalog timestamps"
    )
    if n_prices <= 0:
        px_txt = "no price rows yet (WebSocket / order book path)"
        px_level = "warn"
    elif px_age is None:
        px_txt = "prices table unexpected"
        px_level = "bad"
    elif px_age <= _PRICES_FRESH_SEC:
        px_txt = f"last tick {int(px_age)}s ago"
        px_level = "ok"
    elif px_age <= _PRICES_FRESH_SEC * 4:
        px_txt = f"last tick {int(px_age)}s ago (stale)"
        px_level = "warn"
    else:
        px_txt = f"last tick {int(px_age)}s ago"
        px_level = "bad"

    if n_markets <= 0:
        cat_level = "bad"
        cat_txt = "no markets"
    elif cat_age is None:
        cat_level = "unknown"
    elif cat_age <= _CATALOG_FRESH_SEC:
        cat_level = "ok"
    elif cat_age <= _CATALOG_FRESH_SEC * 5:
        cat_level = "warn"
    else:
        cat_level = "bad"

    def _sev(level: str) -> int:
        return {"ok": 0, "warn": 1, "unknown": 1, "bad": 2}.get(level, 1)

    _ing_sev = max(_sev(cat_level), _sev(px_level))
    ingest_level = "ok" if _ing_sev == 0 else "warn" if _ing_sev == 1 else "bad"
    services.append(
        {
            "id": "ingest",
            "label": "ploy-ingest",
            "level": ingest_level,
            "detail": f"{cat_txt} · {px_txt}",
        }
    )

    # Enrich (optional)
    if settings.enrichment_enabled:
        gs_age = age_sec(g_ts)
        sp = settings.sports_provider.lower()
        odds_like = sp in ("odds", "theodds", "oddsapi")
        n_odds = len(settings.enrichment_odds_sport_keys())
        n_espn = len(settings.enrichment_espn_league_keys())

        def _enrich_source_label() -> str:
            if odds_like:
                return f"Odds API ({n_odds} keys)" if n_odds > 1 else "Odds API"
            if n_espn > 1:
                return f"multi-league ESPN ({n_espn} leagues)"
            return "ESPN"

        if n_game_state <= 0:
            enrich_level, enrich_detail = "bad", "no game_state rows — is enrich running?"
        elif gs_age is None:
            enrich_level, enrich_detail = "unknown", "could not read latest game_state ts"
        elif gs_age <= _GAME_STATE_FRESH_SEC:
            enrich_level = "ok"
            enrich_detail = f"latest {_enrich_source_label()} snapshot {int(gs_age)}s ago"
        elif gs_age <= _GAME_STATE_FRESH_SEC * 10:
            enrich_level = "warn"
            enrich_detail = (
                f"latest {_enrich_source_label()} snapshot {int(gs_age)}s ago (quiet hours / backoff ok)"
            )
        else:
            enrich_level = "bad"
            enrich_detail = f"latest snapshot {int(gs_age)}s ago — enrich may be stopped"
    else:
        enrich_level, enrich_detail = "ok", "optional — disabled (price-only stack)"
    services.append(
        {"id": "enrich", "label": "ploy-enrich", "level": enrich_level, "detail": enrich_detail}
    )

    # Kalshi ingest
    from ploy_agent.kalshi.schema import schema_ready

    if not settings.kalshi_enabled:
        kalshi_level, kalshi_detail = "ok", "disabled (KALSHI_ENABLED=false)"
    elif not await schema_ready(conn):
        kalshi_level, kalshi_detail = "warn", "kalshi tables missing — run ploy-migrate"
    else:
        k_ts = await conn.fetchval("SELECT MAX(ts) FROM kalshi_prices")
        k_age = age_sec(k_ts)
        n_pairs = int(await conn.fetchval("SELECT COUNT(*) FROM cross_venue_pairs WHERE active") or 0)
        if n_pairs <= 0:
            kalshi_level, kalshi_detail = "warn", "no active cross_venue_pairs — run ploy-kalshi load-pairs"
        elif k_age is None:
            kalshi_level, kalshi_detail = "bad", "no kalshi_prices — is kalshi-ingest running?"
        elif k_age <= 60:
            kalshi_level = "ok"
            kalshi_detail = f"latest Kalshi quote {int(k_age)}s ago ({n_pairs} pairs)"
        elif k_age <= 300:
            kalshi_level = "warn"
            kalshi_detail = f"Kalshi quote {int(k_age)}s ago"
        else:
            kalshi_level = "bad"
            kalshi_detail = f"Kalshi stale {int(k_age)}s — check kalshi-ingest"
    services.append(
        {"id": "kalshi", "label": "ploy-kalshi-ingest", "level": kalshi_level, "detail": kalshi_detail}
    )

    # Reason (fair_values)
    fv_age = age_sec(f_ts)
    if n_fair <= 0:
        reason_level, reason_detail = (
            "warn",
            "no fair_values yet — need live prices + ploy-reason",
        )
    elif fv_age is None:
        reason_level, reason_detail = "unknown", "could not read fair_values ts"
    elif fv_age <= _FAIR_FRESH_SEC:
        reason_level = "ok"
        reason_detail = f"last fair value {int(fv_age)}s ago"
    elif fv_age <= _FAIR_FRESH_SEC * 5:
        reason_level = "warn"
        reason_detail = f"last fair value {int(fv_age)}s ago (slow)"
    else:
        reason_level = "bad"
        reason_detail = f"last fair value {int(fv_age)}s ago — reason loop idle?"
    services.append(
        {"id": "reason", "label": "ploy-reason", "level": reason_level, "detail": reason_detail}
    )

    return services


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="Polymarket Edge Agent", lifespan=_lifespan)


def _payload_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _pick_dicts(picks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in picks:
        edge = float(p.edge_cents)
        out.append(
            {
                "strategy_id": p.strategy_id,
                "market_id": p.market_id,
                "question": p.question or "",
                "mid": p.mid,
                "model_prob": p.model_prob,
                "market_prob": p.market_prob,
                "edge_cents": edge,
                "direction": "BUY" if edge >= 0 else "SELL",
                "confidence": p.confidence,
                "reasoning": p.reasoning or "",
                "depth_1c": p.depth_1c,
                "score": p.score,
                "kelly_frac": getattr(p, "kelly_frac", 0.0) or 0.0,
            }
        )
    return out


def _recommendation_dicts(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        pj = _payload_dict(r.get("payload_json"))
        edge = float(pj.get("edge_cents", 0))
        out.append(
            {
                "id": int(r["id"]),
                "ts": r["ts"],
                "market_id": r["market_id"],
                "question": r.get("question") or "",
                "category": r.get("category") or "",
                "strategy_id": r.get("strategy_id") or pj.get("strategy_id") or "",
                "status": r.get("status") or "pending",
                "score": float(r["score"]) if r.get("score") is not None else 0.0,
                "edge_cents": edge,
                "direction": "BUY" if edge >= 0 else "SELL",
                "model_prob": pj.get("model_prob"),
                "market_prob": pj.get("market_prob"),
                "confidence": pj.get("confidence"),
                "reasoning": (pj.get("reasoning") or "")[:400],
            }
        )
    return out


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> Any:
    pool = await get_pool()
    async with pool.acquire() as conn:
        picks = await top_picks(
            conn,
            limit=settings.rank_top_n,
            strategy_ids=settings.strategy_ids(),
            merge_by_market=settings.rank_merge_by_market,
        )
        # Use approximate counts for hypertables to avoid slow full scans
        async def _approx_count(table: str) -> int:
            # TimescaleDB: sum reltuples across all chunks for the hypertable
            approx = await conn.fetchval(
                """
                SELECT COALESCE(SUM(c.reltuples), 0)::bigint
                FROM _timescaledb_catalog.hypertable h
                JOIN _timescaledb_catalog.chunk ch ON ch.hypertable_id = h.id
                JOIN pg_class c ON c.oid = format('%I.%I',
                    ch.schema_name, ch.table_name)::regclass
                WHERE h.table_name = $1
                """,
                table,
            )
            if approx and int(approx) > 0:
                return int(approx)
            # Fallback: plain pg_class (works for non-hypertables)
            approx2 = await conn.fetchval(
                "SELECT reltuples::bigint FROM pg_class WHERE relname = $1", table
            )
            return int(approx2) if approx2 and approx2 > 0 else 0

        stats = {
            "markets": int(await conn.fetchval("SELECT COUNT(*) FROM markets") or 0),
            "prices": await _approx_count("prices"),
            "game_state": await _approx_count("game_state"),
            "fair_values": await _approx_count("fair_values"),
            "recommendations": int(
                await conn.fetchval("SELECT COUNT(*) FROM recommendations") or 0
            ),
        }
        brier = await conn.fetchval(
            """
            SELECT AVG(POWER(model_prob - market_prob, 2))
            FROM (
              SELECT DISTINCT ON (market_id, strategy_id) market_id, strategy_id, model_prob, market_prob
              FROM fair_values
              ORDER BY market_id, strategy_id, ts DESC
            ) x
            """
        )
        stats["brier_vs_market"] = float(brier) if brier is not None else None

        fair_rows = await conn.fetch(
            """
            SELECT f.ts, f.strategy_id, f.market_id, m.question, f.model_prob, f.market_prob,
                   f.edge_cents, f.confidence, LEFT(f.reasoning, 240) AS reasoning
            FROM fair_values f
            JOIN markets m ON m.id = f.market_id
            ORDER BY f.ts DESC
            LIMIT 30
            """
        )
        rec_rows = await conn.fetch(
            """
            SELECT r.id, r.ts, r.market_id, r.strategy_id, r.score, r.status, r.payload_json,
                   m.question, m.category
            FROM recommendations r
            JOIN markets m ON m.id = r.market_id
            ORDER BY
              CASE r.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
              r.score DESC NULLS LAST,
              r.ts DESC
            LIMIT 20
            """
        )
        saved_recommendations = _recommendation_dicts([dict(r) for r in rec_rows])
        game_rows = await conn.fetch(
            """
            WITH latest AS (
              SELECT DISTINCT ON (game_id)
                game_id, ts, home_team, away_team, home_score, away_score,
                period, time_remaining
              FROM game_state
              ORDER BY game_id, ts DESC
            )
            SELECT * FROM latest ORDER BY ts DESC LIMIT 20
            """
        )
        price_ticks = await conn.fetch(
            """
            SELECT p.ts, p.market_id, m.question, m.category, p.bid, p.ask, p.mid, p.depth_1c
            FROM prices p
            JOIN markets m ON m.id = p.market_id
            ORDER BY p.ts DESC
            LIMIT 50
            """
        )
        pipeline_status = await _pipeline_status(
            conn,
            n_markets=stats["markets"],
            n_prices=stats["prices"],
            n_game_state=stats["game_state"],
            n_fair=stats["fair_values"],
        )

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "picks": _pick_dicts(picks),
            "saved_recommendations": saved_recommendations,
            "rank_top_n": settings.rank_top_n,
            "min_edge_cents": settings.min_edge_cents,
            "stats": stats,
            "fair_rows": [dict(r) for r in fair_rows],
            "game_rows": [dict(r) for r in game_rows],
            "price_ticks": [dict(r) for r in price_ticks],
            "pipeline_status": pipeline_status,
            "refresh_sec": 30,
        },
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------- Simulation (paper trading) ----------


@app.get("/api/sim/profiles")
async def sim_profiles() -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, min_edge_cents, min_confidence, min_model_prob,
                   max_open_per_market, cooldown_sec
            FROM sim_profiles ORDER BY id
            """
        )
    profiles: list[dict[str, Any]] = []
    for r in rows:
        edge = float(r["min_edge_cents"])
        conf = float(r["min_confidence"])
        model = float(r["min_model_prob"])
        profiles.append(
            {
                "id": str(r["id"]),
                "min_edge_cents": edge,
                "min_confidence": conf,
                "min_model_prob": model,
                "max_open_per_market": int(r["max_open_per_market"]),
                "cooldown_sec": int(r["cooldown_sec"]),
                "label": (
                    f"≥{edge:.0f}¢ edge · ≥{conf * 100:.0f}% confidence · "
                    f"≥{model * 100:.0f}% model prob"
                ),
            }
        )
    return {"profiles": profiles, "count": len(profiles)}


@app.get("/api/sim/summary")
async def sim_summary(
    profile_id: str | None = None,
    sim_run_id: int | None = None,
    all_runs: bool = False,
) -> dict[str, Any]:
    from ploy_agent.sim.metrics import (
        best_fit_markets,
        compare_profiles,
        group_summary,
        summarize_trades,
        trades_from_rows,
    )
    from ploy_agent.sim import repo as sim_repo

    pool = await get_pool()
    async with pool.acquire() as conn:
        run_id = (
            await sim_repo.resolve_forward_run_id(conn, sim_run_id)
            if not all_runs
            else sim_run_id
        )
        if not all_runs and run_id is None:
            if profile_id:
                return {
                    "profile_id": profile_id,
                    "sim_run_id": None,
                    "totals": summarize_trades([]),
                    "by_category": [],
                    "by_market": [],
                    "by_strategy": [],
                    "best_fit": [],
                }
            return {"compare": [], "sim_run_id": None}

        if profile_id:
            rows = await sim_repo.fetch_trades(
                conn, profile_id=profile_id, sim_run_id=run_id, limit=20_000
            )
            trades = trades_from_rows([dict(r) for r in rows])
            return {
                "profile_id": profile_id,
                "sim_run_id": run_id,
                "totals": summarize_trades(trades),
                "by_category": group_summary(trades, lambda t: t.category),
                "by_market": group_summary(trades, lambda t: t.market_id)[:15],
                "by_strategy": group_summary(trades, lambda t: t.strategy_id),
                "best_fit": best_fit_markets(trades)[:10],
            }
        rows = await sim_repo.fetch_trades(conn, sim_run_id=run_id, limit=50_000)
    trades = trades_from_rows([dict(r) for r in rows])
    return {
        "compare": compare_profiles(trades)[:20],
        "by_strategy": group_summary(trades, lambda t: t.strategy_id),
        "by_category": group_summary(trades, lambda t: t.category),
        "sim_run_id": run_id,
    }


@app.get("/api/sim/series")
async def sim_series(
    profile_id: str,
    sim_run_id: int | None = None,
    all_runs: bool = False,
) -> dict[str, Any]:
    from ploy_agent.sim.metrics import daily_cumulative_series, trades_from_rows
    from ploy_agent.sim import repo as sim_repo

    pool = await get_pool()
    async with pool.acquire() as conn:
        run_id = (
            await sim_repo.resolve_forward_run_id(conn, sim_run_id)
            if not all_runs
            else sim_run_id
        )
        if not all_runs and run_id is None:
            return {"profile_id": profile_id, "sim_run_id": None, "series": []}
        rows = await sim_repo.fetch_trades(
            conn, profile_id=profile_id, sim_run_id=run_id, limit=20_000
        )
    trades = trades_from_rows([dict(r) for r in rows])
    return {
        "profile_id": profile_id,
        "sim_run_id": run_id,
        "series": daily_cumulative_series(trades),
    }


@app.get("/api/sim/trades")
async def sim_trades_list(
    profile_id: str | None = None,
    sim_run_id: int | None = None,
    all_runs: bool = False,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    from ploy_agent.sim import repo as sim_repo
    from ploy_agent.sim.tracker import trade_row_to_dict

    limit = min(max(limit, 1), 1000)
    pool = await get_pool()
    async with pool.acquire() as conn:
        run_id = (
            await sim_repo.resolve_forward_run_id(conn, sim_run_id)
            if not all_runs
            else sim_run_id
        )
        if not all_runs and run_id is None:
            return {"trades": [], "count": 0, "sim_run_id": None}
        rows = await sim_repo.fetch_trades(
            conn,
            profile_id=profile_id,
            sim_run_id=run_id,
            status=status,
            limit=limit,
        )

    trades = [trade_row_to_dict(r) for r in rows]
    return {"trades": trades, "count": len(trades), "sim_run_id": run_id}


@app.get("/api/sim/performance")
async def sim_performance(
    profile_id: str | None = None,
    sim_run_id: int | None = None,
    all_runs: bool = False,
) -> dict[str, Any]:
    """Paper-trading performance: daily rollup, exit breakdown, open positions."""
    from ploy_agent.sim import repo as sim_repo
    from ploy_agent.sim.metrics import trades_from_rows
    from ploy_agent.sim.performance import build_performance_payload
    from ploy_agent.sim.tracker import build_tracker_payload

    pool = await get_pool()
    async with pool.acquire() as conn:
        run_id = (
            await sim_repo.resolve_forward_run_id(conn, sim_run_id)
            if not all_runs
            else sim_run_id
        )
        run = await sim_repo.fetch_latest_forward_run(conn) if not all_runs else None
        if all_runs:
            run = None
        elif run_id is not None:
            run = await conn.fetchrow(
                "SELECT id, started_at, ended_at, mode, notes FROM sim_runs WHERE id = $1",
                run_id,
            )
        totals_row = (
            await sim_repo.fetch_run_totals(conn, run_id) if run_id is not None else None
        )
        if run_id is None:
            return {
                **build_performance_payload([], profile_id=profile_id, sim_run_id=None),
                "tracker": build_tracker_payload(
                    run=None,
                    totals=None,
                    recent_rows=[],
                    now=datetime.now(timezone.utc),
                    sim_forward_run_hours=settings.sim_forward_run_hours,
                ),
            }
        rows = await sim_repo.fetch_trades(
            conn, profile_id=profile_id, sim_run_id=run_id, limit=50_000
        )

    trades = trades_from_rows([dict(r) for r in rows])
    payload = build_performance_payload(
        trades, profile_id=profile_id, sim_run_id=run_id
    )
    payload["tracker"] = build_tracker_payload(
        run=dict(run) if run else None,
        totals=dict(totals_row) if totals_row else None,
        recent_rows=[],
        now=datetime.now(timezone.utc),
        sim_forward_run_hours=settings.sim_forward_run_hours,
    )
    return payload


@app.get("/api/cross-venue/spreads")
async def cross_venue_spreads() -> dict[str, Any]:
    """Live Polymarket vs Kalshi mids for curated cross-venue pairs."""
    from ploy_agent.common.cross_venue import spread_cents
    from ploy_agent.kalshi import repo as krepo
    from ploy_agent.kalshi.schema import schema_ready

    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await schema_ready(conn):
            return {"pairs": [], "count": 0, "note": "kalshi migration not applied"}
        pairs = await krepo.active_pairs(conn)
        rows: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc)
        for p in pairs:
            poly_id = str(p["poly_market_id"])
            ticker = str(p["kalshi_ticker"])
            omap = str(p["outcome_map"])
            poly = await conn.fetchrow(
                """
                SELECT mid, depth_1c, ts FROM prices
                WHERE market_id = $1 AND mid IS NOT NULL
                ORDER BY ts DESC LIMIT 1
                """,
                poly_id,
            )
            kalshi = await krepo.latest_kalshi_price(conn, ticker)
            if poly is None or kalshi is None or poly["mid"] is None or kalshi["mid"] is None:
                continue
            poly_mid = float(poly["mid"])
            kalshi_mid = float(kalshi["mid"])
            gap = spread_cents(poly_mid, kalshi_mid, outcome_map=omap)
            poly_age = (now - poly["ts"]).total_seconds() if poly["ts"] else None
            kalshi_age = (now - kalshi["ts"]).total_seconds() if kalshi["ts"] else None
            rows.append(
                {
                    "pair_id": p["id"],
                    "label": p["label"],
                    "poly_market_id": poly_id,
                    "kalshi_ticker": ticker,
                    "outcome_map": omap,
                    "poly_mid": round(poly_mid, 4),
                    "kalshi_mid": round(kalshi_mid, 4),
                    "spread_cents": round(gap, 2),
                    "poly_age_sec": round(poly_age, 1) if poly_age is not None else None,
                    "kalshi_age_sec": round(kalshi_age, 1) if kalshi_age is not None else None,
                }
            )
    return {"pairs": rows, "count": len(rows)}


@app.get("/api/sim/tracker")
async def sim_tracker() -> dict[str, Any]:
    from ploy_agent.sim import repo as sim_repo
    from ploy_agent.sim.tracker import build_tracker_payload

    pool = await get_pool()
    async with pool.acquire() as conn:
        run = await sim_repo.fetch_latest_forward_run(conn)
        totals = None
        recent: list[Any] = []
        if run is not None:
            rid = int(run["id"])
            totals = await sim_repo.fetch_run_totals(conn, rid)
            recent = await sim_repo.fetch_recent_trades_for_run(conn, rid, limit=20)

    now = datetime.now(timezone.utc)
    payload = build_tracker_payload(
        run=dict(run) if run else None,
        totals=dict(totals) if totals else None,
        recent_rows=recent,
        now=now,
        sim_forward_run_hours=settings.sim_forward_run_hours,
    )
    return payload


@app.get("/api/sim/runs")
async def sim_runs_list(limit: int = 5) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, started_at, ended_at, mode, notes
            FROM sim_runs
            ORDER BY started_at DESC
            LIMIT $1
            """,
            min(max(limit, 1), 20),
        )
        trade_counts = await conn.fetch(
            """
            SELECT sim_run_id, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE status = 'closed') AS closed
            FROM sim_trades
            WHERE sim_run_id IS NOT NULL
            GROUP BY sim_run_id
            """
        )
    counts = {int(r["sim_run_id"]): {"n": int(r["n"]), "closed": int(r["closed"])} for r in trade_counts}
    runs = []
    for r in rows:
        rid = int(r["id"])
        c = counts.get(rid, {"n": 0, "closed": 0})
        runs.append(
            {
                "id": rid,
                "mode": str(r["mode"]),
                "started_at": str(r["started_at"]),
                "ended_at": str(r["ended_at"]) if r.get("ended_at") else None,
                "notes": r.get("notes"),
                "trade_count": c["n"],
                "closed_count": c["closed"],
            }
        )
    return {"runs": runs}


@app.get("/api/top-picks")
async def api_top_picks() -> dict[str, Any]:
    """Live ranked edges (same logic as notifier ranking)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        picks = await top_picks(
            conn,
            limit=settings.rank_top_n,
            strategy_ids=settings.strategy_ids(),
            merge_by_market=settings.rank_merge_by_market,
        )
    return {"picks": _pick_dicts(picks), "rank_top_n": settings.rank_top_n}


@app.get("/api/recommendations")
async def api_recommendations() -> dict[str, Any]:
    """Persisted recommendation rows (Slack/Telegram alerts)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.ts, r.market_id, r.strategy_id, r.score, r.status, r.payload_json,
                   m.question, m.category
            FROM recommendations r
            JOIN markets m ON m.id = r.market_id
            ORDER BY
              CASE r.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
              r.score DESC NULLS LAST,
              r.ts DESC
            LIMIT 20
            """
        )
    return {"recommendations": _recommendation_dicts([dict(r) for r in rows])}


# ---------- SSE (Server-Sent Events) for real-time updates ----------


async def _sse_generator(request: Request):
    """Poll DB every 2 seconds and push changes as SSE events.

    Holds a single DB connection for the lifetime of the SSE stream (Bug 18 fix).
    Detects client disconnect via request.is_disconnected() (Bug 17 fix).
    """
    pool = await get_pool()
    last_price_ts: str | None = None
    last_fv_ts: str | None = None
    last_rec_id: int | None = None

    async with pool.acquire() as conn:
        while not await request.is_disconnected():
            try:
                # Latest price tick
                price_row = await conn.fetchrow(
                    """
                    SELECT p.ts, p.market_id, m.question, m.category, p.bid, p.ask, p.mid, p.depth_1c
                    FROM prices p JOIN markets m ON m.id = p.market_id
                    ORDER BY p.ts DESC LIMIT 1
                    """
                )
                # Latest fair value
                fv_row = await conn.fetchrow(
                    """
                    SELECT f.ts, f.strategy_id, f.market_id, m.question, f.model_prob,
                           f.market_prob, f.edge_cents, f.confidence
                    FROM fair_values f JOIN markets m ON m.id = f.market_id
                    ORDER BY f.ts DESC LIMIT 1
                    """
                )
                # Latest recommendation
                rec_row = await conn.fetchrow(
                    "SELECT id, market_id, score, status, strategy_id, ts FROM recommendations ORDER BY id DESC LIMIT 1"
                )
                # Approximate counts — avoid expensive COUNT(*) on hypertables
                stats = {}
                for tbl in ("markets", "prices", "fair_values", "recommendations"):
                    approx = await conn.fetchval(
                        "SELECT reltuples::bigint FROM pg_class WHERE relname = $1", tbl
                    )
                    stats[tbl] = int(approx) if approx and approx > 0 else 0

                # Emit price tick if new
                if price_row:
                    pts = str(price_row["ts"])
                    if pts != last_price_ts:
                        last_price_ts = pts
                        data = {
                            "ts": pts,
                            "market_id": price_row["market_id"],
                            "question": price_row["question"] or "",
                            "category": price_row["category"] or "",
                            "mid": float(price_row["mid"]) if price_row["mid"] else None,
                            "bid": float(price_row["bid"]) if price_row["bid"] else None,
                            "ask": float(price_row["ask"]) if price_row["ask"] else None,
                        }
                        yield f"event: price_tick\ndata: {json.dumps(data)}\n\n"

                # Emit fair value if new
                if fv_row:
                    fts = str(fv_row["ts"])
                    if fts != last_fv_ts:
                        last_fv_ts = fts
                        data = {
                            "ts": fts,
                            "strategy_id": fv_row["strategy_id"],
                            "market_id": fv_row["market_id"],
                            "question": fv_row["question"] or "",
                            "edge_cents": float(fv_row["edge_cents"]),
                            "confidence": float(fv_row["confidence"]),
                            "model_prob": float(fv_row["model_prob"]),
                            "market_prob": float(fv_row["market_prob"]),
                        }
                        yield f"event: new_signal\ndata: {json.dumps(data)}\n\n"

                # Emit recommendation if new
                if rec_row:
                    rid = int(rec_row["id"])
                    if last_rec_id is None or rid > last_rec_id:
                        last_rec_id = rid
                        data = {
                            "id": rid,
                            "market_id": rec_row["market_id"],
                            "score": float(rec_row["score"]),
                            "status": rec_row["status"],
                            "strategy_id": rec_row["strategy_id"] or "",
                        }
                        yield f"event: recommendation_update\ndata: {json.dumps(data)}\n\n"

                # Always emit stats as heartbeat
                yield f"event: pipeline_status\ndata: {json.dumps(stats)}\n\n"

            except asyncio.CancelledError:
                return
            except Exception:
                yield f"event: error\ndata: {json.dumps({'msg': 'db_poll_failed'})}\n\n"

            await asyncio.sleep(2.0)


@app.get("/events")
async def sse_events(request: Request):
    """Server-Sent Events endpoint for real-time dashboard updates."""
    return StreamingResponse(
        _sse_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------- P&L API ----------


@app.get("/api/pnl")
async def pnl_data() -> dict[str, Any]:
    """Return P&L summary for resolved approved recommendations.

    Deduplicated: each (market_id, strategy_id) counts as ONE position.
    Uses the first recommendation per position (earliest entry).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Deduplicate: one row per (market_id, strategy_id), keep first entry
        rows = await conn.fetch(
            """
            WITH deduped AS (
              SELECT DISTINCT ON (market_id, COALESCE(strategy_id, ''))
                id, market_id, resolved_at, pnl_cents, edge_direction,
                entry_price, resolved_outcome, strategy_id, score
              FROM recommendations
              WHERE pnl_cents IS NOT NULL
              ORDER BY market_id, COALESCE(strategy_id, ''), id ASC
            )
            SELECT * FROM deduped ORDER BY resolved_at ASC
            """
        )
        # Summary stats (deduplicated)
        summary = await conn.fetchrow(
            """
            WITH deduped AS (
              SELECT DISTINCT ON (market_id, COALESCE(strategy_id, ''))
                pnl_cents, strategy_id
              FROM recommendations
              WHERE pnl_cents IS NOT NULL
              ORDER BY market_id, COALESCE(strategy_id, ''), id ASC
            )
            SELECT COALESCE(SUM(pnl_cents), 0) AS total_pnl,
                   COUNT(*) AS total_resolved,
                   COUNT(*) FILTER (WHERE pnl_cents > 0) AS wins
            FROM deduped
            """
        )
        total_pnl = float(summary["total_pnl"])
        total_resolved = int(summary["total_resolved"])
        wins = int(summary["wins"])

        # Per-strategy breakdown (deduplicated)
        strategy_rows = await conn.fetch(
            """
            WITH deduped AS (
              SELECT DISTINCT ON (market_id, COALESCE(strategy_id, ''))
                pnl_cents, strategy_id
              FROM recommendations
              WHERE pnl_cents IS NOT NULL
              ORDER BY market_id, COALESCE(strategy_id, ''), id ASC
            )
            SELECT strategy_id,
                   COUNT(*) AS n,
                   SUM(pnl_cents) AS total_pnl,
                   COUNT(*) FILTER (WHERE pnl_cents > 0) AS wins
            FROM deduped
            GROUP BY strategy_id
            """
        )

    cumulative: list[dict[str, Any]] = []
    running = 0.0
    for r in rows:
        running += float(r["pnl_cents"])
        cumulative.append({
            "id": int(r["id"]),
            "resolved_at": str(r["resolved_at"]),
            "pnl_cents": float(r["pnl_cents"]),
            "cumulative": round(running, 2),
            "direction": r["edge_direction"],
            "strategy_id": r["strategy_id"] or "",
        })

    strategies = []
    for sr in strategy_rows:
        n = int(sr["n"])
        strategies.append({
            "strategy_id": sr["strategy_id"] or "unknown",
            "n": n,
            "total_pnl": round(float(sr["total_pnl"]), 2),
            "wins": int(sr["wins"]),
            "win_rate": round(int(sr["wins"]) / n, 3) if n > 0 else 0,
        })

    return {
        "total_pnl_cents": round(float(total_pnl), 2),
        "total_resolved": total_resolved,
        "wins": wins,
        "win_rate": round(wins / total_resolved, 3) if total_resolved > 0 else 0,
        "cumulative": cumulative,
        "strategies": strategies,
    }


# ---------- Accuracy / Brier Score API ----------


@app.get("/api/accuracy")
async def accuracy_data() -> dict[str, Any]:
    """Per-strategy Brier scores: model_prob vs resolved_outcome (ground truth)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Join latest fair_value per (market, strategy) with resolved recommendations
        rows = await conn.fetch(
            """
            WITH latest_fv AS (
              SELECT DISTINCT ON (market_id, strategy_id)
                     market_id, strategy_id, model_prob, market_prob
              FROM fair_values
              ORDER BY market_id, strategy_id, ts DESC
            )
            SELECT fv.strategy_id,
                   fv.model_prob,
                   fv.market_prob,
                   r.resolved_outcome
            FROM latest_fv fv
            JOIN recommendations r ON r.market_id = fv.market_id
                                  AND r.strategy_id = fv.strategy_id
            WHERE r.resolved_outcome IS NOT NULL
            """
        )
        # Also get overall model-vs-market Brier (existing metric, for context)
        overall_brier = await conn.fetchval(
            """
            SELECT AVG(POWER(model_prob - market_prob, 2))
            FROM (
              SELECT DISTINCT ON (market_id, strategy_id) model_prob, market_prob
              FROM fair_values
              ORDER BY market_id, strategy_id, ts DESC
            ) x
            """
        )

    if not rows:
        return {
            "has_data": False,
            "message": "No resolved markets yet — accuracy tracking starts once picks resolve.",
            "overall_brier_vs_market": float(overall_brier) if overall_brier else None,
            "strategies": [],
            "totals": {},
        }

    # Per-strategy aggregation
    from collections import defaultdict

    strats: dict[str, list[dict[str, float]]] = defaultdict(list)
    for r in rows:
        strats[r["strategy_id"]].append({
            "model_prob": float(r["model_prob"]),
            "market_prob": float(r["market_prob"]),
            "outcome": int(r["resolved_outcome"]),
        })

    strategy_results = []
    all_model_errors = []
    all_market_errors = []

    for sid, entries in sorted(strats.items()):
        n = len(entries)
        model_brier = sum((e["model_prob"] - e["outcome"]) ** 2 for e in entries) / n
        market_brier = sum((e["market_prob"] - e["outcome"]) ** 2 for e in entries) / n
        # Calibration: did model_prob > 0.5 predict the right outcome?
        correct = sum(
            1 for e in entries
            if (e["model_prob"] >= 0.5 and e["outcome"] == 1)
            or (e["model_prob"] < 0.5 and e["outcome"] == 0)
        )
        strategy_results.append({
            "strategy_id": sid,
            "n": n,
            "brier_model": round(model_brier, 4),
            "brier_market": round(market_brier, 4),
            "edge_vs_market": round(market_brier - model_brier, 4),  # positive = model better
            "accuracy_pct": round(correct / n * 100, 1) if n > 0 else 0,
        })
        all_model_errors.extend((e["model_prob"] - e["outcome"]) ** 2 for e in entries)
        all_market_errors.extend((e["market_prob"] - e["outcome"]) ** 2 for e in entries)

    total_n = len(all_model_errors)
    totals = {
        "n": total_n,
        "brier_model": round(sum(all_model_errors) / total_n, 4) if total_n else None,
        "brier_market": round(sum(all_market_errors) / total_n, 4) if total_n else None,
        "edge_vs_market": round(
            (sum(all_market_errors) - sum(all_model_errors)) / total_n, 4
        ) if total_n else None,
    }

    return {
        "has_data": True,
        "overall_brier_vs_market": float(overall_brier) if overall_brier else None,
        "strategies": strategy_results,
        "totals": totals,
    }


# ---------- Calibration Curve API ----------


@app.get("/api/calibration")
async def calibration_data() -> dict[str, Any]:
    """Calibration curve: bucketed predicted probability vs actual outcome rate."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH latest_fv AS (
              SELECT DISTINCT ON (market_id, strategy_id)
                     market_id, strategy_id, model_prob, market_prob
              FROM fair_values
              ORDER BY market_id, strategy_id, ts DESC
            )
            SELECT fv.strategy_id, fv.model_prob, fv.market_prob, r.resolved_outcome
            FROM latest_fv fv
            JOIN recommendations r ON r.market_id = fv.market_id
                                  AND r.strategy_id = fv.strategy_id
            WHERE r.resolved_outcome IS NOT NULL
            """
        )

    if not rows:
        return {"has_data": False, "buckets": [], "strategies": {}}

    # Build calibration buckets
    bucket_edges = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
    all_entries = [
        {"model_prob": float(r["model_prob"]), "market_prob": float(r["market_prob"]),
         "outcome": int(r["resolved_outcome"]), "strategy_id": r["strategy_id"]}
        for r in rows
    ]

    # Overall calibration
    buckets = []
    for i in range(len(bucket_edges) - 1):
        lo, hi = bucket_edges[i], bucket_edges[i + 1]
        in_bucket = [e for e in all_entries if lo <= e["model_prob"] < hi]
        if not in_bucket:
            continue
        avg_pred = sum(e["model_prob"] for e in in_bucket) / len(in_bucket)
        actual_rate = sum(e["outcome"] for e in in_bucket) / len(in_bucket)
        mkt_pred = sum(e["market_prob"] for e in in_bucket) / len(in_bucket)
        buckets.append({
            "range": f"{lo:.1f}-{hi:.1f}",
            "n": len(in_bucket),
            "avg_predicted": round(avg_pred, 3),
            "actual_rate": round(actual_rate, 3),
            "market_predicted": round(mkt_pred, 3),
        })

    # Per-strategy calibration
    from collections import defaultdict
    strat_entries: dict[str, list] = defaultdict(list)
    for e in all_entries:
        strat_entries[e["strategy_id"]].append(e)

    strategies = {}
    for sid, entries in strat_entries.items():
        strat_buckets = []
        for i in range(len(bucket_edges) - 1):
            lo, hi = bucket_edges[i], bucket_edges[i + 1]
            in_b = [e for e in entries if lo <= e["model_prob"] < hi]
            if not in_b:
                continue
            strat_buckets.append({
                "range": f"{lo:.1f}-{hi:.1f}",
                "n": len(in_b),
                "avg_predicted": round(sum(e["model_prob"] for e in in_b) / len(in_b), 3),
                "actual_rate": round(sum(e["outcome"] for e in in_b) / len(in_b), 3),
            })
        strategies[sid] = strat_buckets

    return {"has_data": True, "buckets": buckets, "strategies": strategies}


# ---------- Full Analytics API ----------


@app.get("/api/analytics")
async def analytics_data() -> dict[str, Any]:
    """Comprehensive analytics: every trade, breakdowns by strategy/sport/market-type, P&L."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Deduplicated: one row per (market_id, strategy_id) position.
        # Keeps the FIRST recommendation (earliest entry) per position.
        rows = await conn.fetch(
            """
            WITH deduped AS (
              SELECT DISTINCT ON (market_id, COALESCE(strategy_id, ''))
                id, ts, market_id, strategy_id, score, status,
                pnl_cents, resolved_outcome, entry_price, edge_direction,
                resolved_at, payload_json, question, category, market_type
              FROM recommendations
              ORDER BY market_id, COALESCE(strategy_id, ''), id ASC
            )
            SELECT d.id, d.ts, d.market_id, d.strategy_id, d.score, d.status,
                   d.pnl_cents, d.resolved_outcome, d.entry_price, d.edge_direction,
                   d.resolved_at, d.payload_json,
                   COALESCE(d.question, m.question) AS question,
                   COALESCE(d.category, m.category) AS category,
                   d.market_type,
                   lp.mid AS current_mid
            FROM deduped d
            JOIN markets m ON m.id = d.market_id
            LEFT JOIN LATERAL (
              SELECT mid FROM prices
              WHERE market_id = d.market_id AND mid IS NOT NULL
              ORDER BY ts DESC LIMIT 1
            ) lp ON true
            ORDER BY d.ts DESC
            LIMIT 2000
            """
        )

    from ploy_agent.common.market_type import classify_market_type, classify_sport_category

    trades: list[dict[str, Any]] = []
    resolved_trades: list[dict[str, Any]] = []
    by_strategy: dict[str, dict[str, Any]] = {}
    by_category: dict[str, dict[str, Any]] = {}
    by_market_type: dict[str, dict[str, Any]] = {}
    total_pnl = 0.0
    total_unrealized = 0.0
    total_open = 0
    total_resolved = 0
    total_wins = 0
    total_losses = 0
    biggest_win = 0.0
    biggest_loss = 0.0
    current_streak = 0
    streak_type = ""
    streak_locked = False  # once the streak direction changes, stop counting
    total_capital_deployed = 0.0  # sum of capital risked across all trades

    for r in rows:
        pj = r["payload_json"] or {}
        if isinstance(pj, str):
            import json as _json
            pj = _json.loads(pj)

        q = r["question"] or ""
        cat = classify_sport_category(r["category"], q)
        mt = r["market_type"] or classify_market_type(q)
        edge = float(pj.get("edge_cents", 0))
        pnl = float(r["pnl_cents"]) if r["pnl_cents"] is not None else None
        outcome = r["resolved_outcome"]
        is_resolved = pnl is not None

        # $1 per trade P&L
        pnl_dollars = round(pnl / 100, 4) if pnl is not None else None

        # Capital deployed per trade: BUY pays entry_price, SELL pays (1-entry_price)
        entry = float(r["entry_price"]) if r.get("entry_price") else None
        if entry is not None:
            capital_cents = entry * 100 if edge >= 0 else (1.0 - entry) * 100
            capital_dollars = round(capital_cents / 100, 4)
        else:
            capital_cents = 0.0
            capital_dollars = 0.0
        total_capital_deployed += capital_dollars

        # Unrealized P&L: compare entry to current mid
        unrealized = None
        current_mid = float(r["current_mid"]) if r.get("current_mid") is not None else None
        if current_mid is not None and entry is not None and pnl is None:
            if edge >= 0:  # BUY
                unrealized = round((current_mid - entry) * 100, 2)
            else:  # SELL
                unrealized = round((entry - current_mid) * 100, 2)

        trade = {
            "id": int(r["id"]),
            "ts": str(r["ts"]),
            "market_id": r["market_id"],
            "question": q[:200],
            "strategy_id": r["strategy_id"] or "",
            "category": cat,
            "market_type": mt,
            "status": r["status"],
            "direction": "BUY" if edge >= 0 else "SELL",
            "edge_cents": round(edge, 2),
            "model_prob": pj.get("model_prob"),
            "market_prob": pj.get("market_prob"),
            "confidence": pj.get("confidence"),
            "entry_price": float(r["entry_price"]) if r["entry_price"] else None,
            "resolved_outcome": outcome,
            "pnl_cents": round(pnl, 2) if pnl is not None else None,
            "pnl_dollars": pnl_dollars,
            "unrealized_pnl_cents": unrealized,
            "current_mid": current_mid,
            "resolved_at": str(r["resolved_at"]) if r["resolved_at"] else None,
            "correct": (pnl > 0) if pnl is not None else None,
            "score": round(float(r["score"]), 2) if r["score"] else 0,
            "capital_cents": round(capital_cents, 2),
            "capital_dollars": capital_dollars,
            "roi_pct": round(pnl / capital_cents * 100, 1) if pnl is not None and capital_cents > 0 else None,
        }
        trades.append(trade)

        # Track unrealized P&L for open positions
        if unrealized is not None:
            total_unrealized += unrealized
            total_open += 1

        if not is_resolved:
            continue

        resolved_trades.append(trade)
        total_resolved += 1
        total_pnl += pnl
        is_win = pnl > 0
        if is_win:
            total_wins += 1
            biggest_win = max(biggest_win, pnl)
        else:
            total_losses += 1
            biggest_loss = min(biggest_loss, pnl)

        # Streak tracking (resolved trades are newest-first)
        if not streak_locked:
            if total_resolved == 1:
                streak_type = "win" if is_win else "loss"
                current_streak = 1
            elif (is_win and streak_type == "win") or (not is_win and streak_type == "loss"):
                current_streak += 1
            else:
                streak_locked = True  # direction changed, freeze the streak

        # Strategy breakdown
        sid = r["strategy_id"] or "unknown"
        s = by_strategy.setdefault(sid, {"n": 0, "wins": 0, "pnl": 0.0, "trades": []})
        s["n"] += 1
        s["pnl"] += pnl
        if is_win:
            s["wins"] += 1

        # Category breakdown
        c = by_category.setdefault(cat, {"n": 0, "wins": 0, "pnl": 0.0})
        c["n"] += 1
        c["pnl"] += pnl
        if is_win:
            c["wins"] += 1

        # Market type breakdown
        m = by_market_type.setdefault(mt, {"n": 0, "wins": 0, "pnl": 0.0})
        m["n"] += 1
        m["pnl"] += pnl
        if is_win:
            m["wins"] += 1

    def _breakdown(d: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for key, v in sorted(d.items(), key=lambda x: x[1]["pnl"], reverse=True):
            n = v["n"]
            out.append({
                "key": key,
                "trades": n,
                "wins": v["wins"],
                "losses": n - v["wins"],
                "win_rate": round(v["wins"] / n * 100, 1) if n else 0,
                "pnl_cents": round(v["pnl"], 2),
                "pnl_dollars": round(v["pnl"] / 100, 2),
                "avg_pnl_cents": round(v["pnl"] / n, 2) if n else 0,
            })
        return out

    win_rate = round(total_wins / total_resolved * 100, 1) if total_resolved else 0

    return {
        "overview": {
            "total_recommendations": len(trades),
            "total_resolved": total_resolved,
            "total_pending": len(trades) - total_resolved,
            "wins": total_wins,
            "losses": total_losses,
            "win_rate": win_rate,
            "total_pnl_cents": round(total_pnl, 2),
            "total_pnl_dollars": round(total_pnl / 100, 2),
            "avg_pnl_cents": round(total_pnl / total_resolved, 2) if total_resolved else 0,
            "biggest_win_cents": round(biggest_win, 2),
            "biggest_loss_cents": round(biggest_loss, 2),
            "current_streak": current_streak,
            "streak_type": streak_type,
            "open_positions": total_open,
            "unrealized_pnl_cents": round(total_unrealized, 2),
            "unrealized_pnl_dollars": round(total_unrealized / 100, 2),
            "total_capital_deployed": round(total_capital_deployed, 2),
            "roi_pct": round(total_pnl / (total_capital_deployed * 100) * 100, 1) if total_capital_deployed > 0 else 0,
        },
        "by_strategy": _breakdown(by_strategy),
        "by_category": _breakdown(by_category),
        "by_market_type": _breakdown(by_market_type),
        "trades": trades[:500],
        "resolved_trades": resolved_trades[:200],
    }


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request) -> Any:
    return templates.TemplateResponse(request, "analytics.html", {})


@app.get("/paper", response_class=HTMLResponse)
async def paper_trading_page(request: Request) -> Any:
    """Paper-trading performance: decision ledger, daily stats, exit rules."""
    return templates.TemplateResponse(request, "paper.html", {})


def run() -> None:
    import uvicorn

    uvicorn.run(
        "ploy_agent.web.app:app",
        host=settings.web_host,
        port=settings.web_port,
        reload=False,
    )
