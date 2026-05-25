"""Notification events server — receives Slack interactions and Telegram webhook callbacks."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.notifier import repo as rec_repo
from ploy_agent.notifier.slack import update_message_status as slack_update
from ploy_agent.notifier.telegram import answer_callback, update_message_status as tg_update

log = get_logger("events_server")


@asynccontextmanager
async def _lifespan(application: FastAPI):
    configure_logging()
    application.state.pool = await get_pool()
    application.state.http = httpx.AsyncClient()
    yield
    await application.state.http.aclose()
    await close_pool()


app = FastAPI(title="PloyAgent Events Server", lifespan=_lifespan)


def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify Slack request signature to prevent forged requests."""
    signing_secret = settings.slack_signing_secret
    if not signing_secret:
        # No signing secret configured — skip verification (dev mode)
        return True
    if abs(time.time() - int(timestamp)) > 300:
        return False  # Reject requests older than 5 minutes (replay attack)
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    computed = "v0=" + hmac.new(
        signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


# ---------- Slack ----------


@app.post("/slack/events")
async def slack_events(request: Request) -> JSONResponse:
    raw_body = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp", "0")
    sig = request.headers.get("X-Slack-Signature", "")
    if not _verify_slack_signature(raw_body, ts, sig):
        return JSONResponse({"error": "invalid_signature"}, status_code=403)
    body = json.loads(raw_body)
    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body["challenge"]})
    return JSONResponse({"ok": True})


@app.post("/slack/interactions")
async def slack_interactions(request: Request, payload: str = Form(...)) -> JSONResponse:
    raw_body = await request.body()
    ts = request.headers.get("X-Slack-Request-Timestamp", "0")
    sig = request.headers.get("X-Slack-Signature", "")
    if not _verify_slack_signature(raw_body, ts, sig):
        return JSONResponse({"error": "invalid_signature"}, status_code=403)
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
        msg_ts = data.get("message", {}).get("ts") or data.get("container", {}).get("message_ts", "")

        if channel and msg_ts:
            await slack_update(
                app.state.http,
                channel,
                msg_ts,
                rec_id,
                status,
                user,
                blocks=data.get("message", {}).get("blocks"),
            )

        log.info("recommendation_actioned", rec_id=rec_id, status=status, user=user, source="slack")

    return JSONResponse({"ok": True})


# ---------- Telegram ----------


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> JSONResponse:
    """Handle Telegram Bot API webhook updates (callback_query from inline buttons)."""
    body = await request.json()
    callback = body.get("callback_query")
    if not callback:
        return JSONResponse({"ok": True})

    cb_data = callback.get("data", "")
    cb_id = callback.get("id", "")
    user_info = callback.get("from", {})
    user = user_info.get("username") or user_info.get("first_name") or str(user_info.get("id", "unknown"))
    message = callback.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    message_id = message.get("message_id")

    # Parse callback data: "approve:123" or "reject:123"
    if ":" not in cb_data:
        return JSONResponse({"ok": True})

    action, _, rec_id_str = cb_data.partition(":")
    if action not in ("approve", "reject"):
        return JSONResponse({"ok": True})

    try:
        rec_id = int(rec_id_str)
    except (ValueError, TypeError):
        return JSONResponse({"ok": True})

    status = "approved" if action == "approve" else "rejected"

    pool = app.state.pool
    async with pool.acquire() as conn:
        await rec_repo.set_status(conn, rec_id, status, notes=f"by telegram user {user}")

    # Update the message to show status
    if chat_id and message_id:
        await tg_update(app.state.http, chat_id, message_id, rec_id, status, user)

    # Acknowledge the button press
    await answer_callback(app.state.http, cb_id, f"Rec #{rec_id} {status}")

    log.info("recommendation_actioned", rec_id=rec_id, status=status, user=user, source="telegram")
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
