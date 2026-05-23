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

    # Enrich
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
        enrich_detail = f"latest {_enrich_source_label()} snapshot {int(gs_age)}s ago (quiet hours / backoff ok)"
    else:
        enrich_level = "bad"
        enrich_detail = f"latest snapshot {int(gs_age)}s ago — enrich may be stopped"
    services.append(
        {"id": "enrich", "label": "ploy-enrich", "level": enrich_level, "detail": enrich_detail}
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
        stats = {
            "markets": int(await conn.fetchval("SELECT COUNT(*) FROM markets") or 0),
            "prices": int(await conn.fetchval("SELECT COUNT(*) FROM prices") or 0),
            "game_state": int(await conn.fetchval("SELECT COUNT(*) FROM game_state") or 0),
            "fair_values": int(await conn.fetchval("SELECT COUNT(*) FROM fair_values") or 0),
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
async def sim_summary(profile_id: str | None = None) -> dict[str, Any]:
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
        if profile_id:
            rows = await sim_repo.fetch_trades(conn, profile_id=profile_id, limit=20_000)
            trades = trades_from_rows([dict(r) for r in rows])
            return {
                "profile_id": profile_id,
                "totals": summarize_trades(trades),
                "by_category": group_summary(trades, lambda t: t.category),
                "by_market": group_summary(trades, lambda t: t.market_id)[:15],
                "by_strategy": group_summary(trades, lambda t: t.strategy_id),
                "best_fit": best_fit_markets(trades)[:10],
            }
        rows = await sim_repo.fetch_trades(conn, limit=50_000)
    trades = trades_from_rows([dict(r) for r in rows])
    return {"compare": compare_profiles(trades)[:20]}


@app.get("/api/sim/series")
async def sim_series(profile_id: str) -> dict[str, Any]:
    from ploy_agent.sim.metrics import daily_cumulative_series, trades_from_rows
    from ploy_agent.sim import repo as sim_repo

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await sim_repo.fetch_trades(conn, profile_id=profile_id, limit=20_000)
    trades = trades_from_rows([dict(r) for r in rows])
    return {"profile_id": profile_id, "series": daily_cumulative_series(trades)}


@app.get("/api/sim/trades")
async def sim_trades_list(
    profile_id: str | None = None,
    sim_run_id: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    from ploy_agent.sim import repo as sim_repo
    from ploy_agent.sim.tracker import trade_row_to_dict

    limit = min(max(limit, 1), 200)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await sim_repo.fetch_trades(
            conn,
            profile_id=profile_id,
            sim_run_id=sim_run_id,
            limit=limit,
        )

    trades = [trade_row_to_dict(r) for r in rows]
    return {"trades": trades, "count": len(trades)}


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


async def _sse_generator():
    """Poll DB every 2 seconds and push changes as SSE events."""
    pool = await get_pool()
    last_price_ts: str | None = None
    last_fv_ts: str | None = None
    last_rec_id: int | None = None

    while True:
        try:
            async with pool.acquire() as conn:
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
async def sse_events():
    """Server-Sent Events endpoint for real-time dashboard updates."""
    return StreamingResponse(
        _sse_generator(),
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
    """Return P&L summary for resolved approved recommendations."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, market_id, resolved_at, pnl_cents, edge_direction,
                   entry_price, resolved_outcome, strategy_id, score
            FROM recommendations
            WHERE pnl_cents IS NOT NULL
            ORDER BY resolved_at ASC
            """
        )
        # Summary stats
        total_pnl = await conn.fetchval(
            "SELECT COALESCE(SUM(pnl_cents), 0) FROM recommendations WHERE pnl_cents IS NOT NULL"
        )
        total_resolved = await conn.fetchval(
            "SELECT COUNT(*) FROM recommendations WHERE pnl_cents IS NOT NULL"
        )
        wins = await conn.fetchval(
            "SELECT COUNT(*) FROM recommendations WHERE pnl_cents IS NOT NULL AND pnl_cents > 0"
        )
        # Per-strategy breakdown
        strategy_rows = await conn.fetch(
            """
            SELECT strategy_id,
                   COUNT(*) AS n,
                   SUM(pnl_cents) AS total_pnl,
                   COUNT(*) FILTER (WHERE pnl_cents > 0) AS wins
            FROM recommendations
            WHERE pnl_cents IS NOT NULL
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
        "total_resolved": int(total_resolved),
        "wins": int(wins),
        "win_rate": round(int(wins) / int(total_resolved), 3) if int(total_resolved) > 0 else 0,
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


def run() -> None:
    import uvicorn

    uvicorn.run(
        "ploy_agent.web.app:app",
        host=settings.web_host,
        port=settings.web_port,
        reload=False,
    )
