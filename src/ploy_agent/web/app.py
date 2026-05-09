from __future__ import annotations

from contextlib import asynccontextmanager
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
            "recommendations": int(await conn.fetchval("SELECT COUNT(*) FROM recommendations") or 0),
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

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "picks": _pick_dicts(picks),
            "stats": stats,
            "fair_rows": [dict(r) for r in fair_rows],
            "rec_rows": rec_display,
            "game_rows": [dict(r) for r in game_rows],
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
