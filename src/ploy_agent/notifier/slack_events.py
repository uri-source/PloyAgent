"""Slack interactivity endpoint — receives approve/reject button clicks."""

from __future__ import annotations

import json
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.notifier import repo as rec_repo
from ploy_agent.notifier.slack import update_message_status

log = get_logger("slack_events")

app = FastAPI(title="PloyAgent Slack Events")


@app.on_event("startup")
async def _startup() -> None:
    configure_logging()
    app.state.pool = await get_pool()
    app.state.http = httpx.AsyncClient()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await app.state.http.aclose()
    await close_pool()


@app.post("/slack/events")
async def slack_events(request: Request) -> JSONResponse:
    body = await request.json()
    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body["challenge"]})
    return JSONResponse({"ok": True})


@app.post("/slack/interactions")
async def slack_interactions(payload: str = Form(...)) -> JSONResponse:
    data: dict[str, Any] = json.loads(payload)
    actions = data.get("actions") or []
    user = data.get("user", {}).get("id", "unknown")

    for action in actions:
        action_id = action.get("action_id", "")
        rec_id_str = action.get("value", "")
        if action_id not in ("rec_approve", "rec_reject"):
            continue
        try:
            rec_id = int(rec_id_str)
        except (ValueError, TypeError):
            continue

        status = "approved" if action_id == "rec_approve" else "rejected"

        pool = app.state.pool
        async with pool.acquire() as conn:
            await rec_repo.set_status(conn, rec_id, status, notes=f"by slack user {user}")

        channel = data.get("channel", {}).get("id") or data.get("container", {}).get("channel_id", "")
        ts = data.get("message", {}).get("ts") or data.get("container", {}).get("message_ts", "")

        if channel and ts:
            await update_message_status(
                app.state.http, channel, ts, rec_id, status, user
            )

        log.info("recommendation_actioned", rec_id=rec_id, status=status, user=user)

    return JSONResponse({"ok": True})


def run() -> None:
    configure_logging()
    uvicorn.run(
        "ploy_agent.notifier.slack_events:app",
        host=settings.web_host,
        port=settings.slack_events_port,
        log_level="info",
    )


if __name__ == "__main__":
    run()
