from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.notifier.rank import top_picks

_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_ROOT / "templates"))

_CATALOG_FRESH_SEC = 180.0
_PRICES_FRESH_SEC = 300.0
_GAME_STATE_FRESH_SEC = 180.0
_FAIR_FRESH_SEC = 120.0


def _tail_agent_log(max_lines: int = 120, max_bytes: int = 96_000) -> list[str]:
    path = Path(settings.agent_log_file)
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        read_n = min(max_bytes, max(size, 0))
        with path.open("rb") as f:
            if size > read_n:
                f.seek(size - read_n)
            raw = f.read().decode("utf-8", errors="replace")
        lines = raw.splitlines()
        return lines[-max_lines:] if len(lines) > max_lines else lines
    except OSError:
        return []


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


def _pick_dicts(picks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in picks:
        out.append(
            {
                "strategy_id": p.strategy_id,
                "market_id": p.market_id,
                "question": p.question or "",
                "mid": p.mid,
                "model_prob": p.model_prob,
                "market_prob": p.market_prob,
                "edge_cents": p.edge_cents,
                "confidence": p.confidence,
                "reasoning": p.reasoning or "",
                "depth_1c": p.depth_1c,
                "score": p.score,
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
            SELECT id, ts, market_id, strategy_id, score, status, payload_json
            FROM recommendations
            ORDER BY ts DESC
            LIMIT 40
            """
        )
        rec_display = []
        for r in rec_rows:
            d = dict(r)
            pj = d.get("payload_json")
            if isinstance(pj, (dict, list)):
                d["payload_json"] = json.dumps(pj)
            rec_display.append(d)
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

    log_lines = _tail_agent_log()
    log_path_display = str(Path(settings.agent_log_file))

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "picks": _pick_dicts(picks),
            "stats": stats,
            "fair_rows": [dict(r) for r in fair_rows],
            "rec_rows": rec_display,
            "game_rows": [dict(r) for r in game_rows],
            "price_ticks": [dict(r) for r in price_ticks],
            "pipeline_status": pipeline_status,
            "log_lines": log_lines,
            "log_path_display": log_path_display,
            "refresh_sec": 30,
        },
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def run() -> None:
    import uvicorn

    uvicorn.run(
        "ploy_agent.web.app:app",
        host=settings.web_host,
        port=settings.web_port,
        reload=False,
    )
