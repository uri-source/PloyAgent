"""Telegram Bot API integration for posting recommendations with approve/reject buttons."""

from __future__ import annotations

from typing import Any

import httpx

from ploy_agent.common.config import settings
from ploy_agent.common.logging_config import get_logger
from ploy_agent.notifier.rank import RankedPick

log = get_logger("notifier.telegram")

_BASE = "https://api.telegram.org/bot{token}"


def _api_url(method: str) -> str:
    return f"{_BASE.format(token=settings.telegram_bot_token)}/{method}"


def _format_pick(pick: RankedPick, rec_id: int) -> str:
    edge_dir = "🟢 BUY" if pick.edge_cents > 0 else "🔴 SELL"
    edge_abs = abs(pick.edge_cents)
    q = pick.question or pick.market_id

    return (
        f"<b>{edge_dir}: {q[:200]}</b>\n\n"
        f"Edge: <b>{edge_abs:.1f}¢</b>  |  Model: {pick.model_prob:.1%}  |  Market: {pick.market_prob:.1%}\n"
        f"Confidence: {pick.confidence:.0%}  |  Depth: {pick.depth_1c:.0f}  |  Score: {pick.score:.2f}\n"
        f"Strategy: <code>{pick.strategy_id}</code>\n\n"
        f"<i>{(pick.reasoning or 'No reasoning')[:400]}</i>"
    )


def _inline_keyboard(rec_id: int) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approve:{rec_id}"},
                {"text": "❌ Reject", "callback_data": f"reject:{rec_id}"},
            ]
        ]
    }


async def post_picks(
    client: httpx.AsyncClient,
    picks: list[tuple[RankedPick, int]],
) -> list[tuple[int, int]]:
    """Post picks to Telegram. Returns list of (rec_id, message_id)."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return []

    results: list[tuple[int, int]] = []

    # Send header
    header = f"📊 <b>Top {len(picks)} Polymarket Edges</b>"
    await client.post(
        _api_url("sendMessage"),
        json={
            "chat_id": settings.telegram_chat_id,
            "text": header,
            "parse_mode": "HTML",
        },
        timeout=15.0,
    )

    # Send each pick as a separate message with inline buttons
    for pick, rec_id in picks:
        text = _format_pick(pick, rec_id)
        payload = {
            "chat_id": settings.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": _inline_keyboard(rec_id),
        }
        r = await client.post(_api_url("sendMessage"), json=payload, timeout=15.0)
        data = r.json()
        if data.get("ok"):
            msg_id = data["result"]["message_id"]
            results.append((rec_id, msg_id))
            log.info("telegram_posted", rec_id=rec_id, message_id=msg_id)
        else:
            log.warning("telegram_post_failed", error=data.get("description"), rec_id=rec_id)

    return results


async def update_message_status(
    client: httpx.AsyncClient,
    chat_id: str,
    message_id: int,
    rec_id: int,
    status: str,
    user: str,
) -> None:
    """Edit a Telegram message to show approval/rejection status."""
    emoji = "✅" if status == "approved" else "❌"
    text = f"{emoji} Recommendation #{rec_id} <b>{status}</b> by {user}"
    await client.post(
        _api_url("editMessageText"),
        json={
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        },
        timeout=10.0,
    )


async def answer_callback(client: httpx.AsyncClient, callback_query_id: str, text: str) -> None:
    """Acknowledge a callback query (dismiss the loading spinner on the button)."""
    await client.post(
        _api_url("answerCallbackQuery"),
        json={"callback_query_id": callback_query_id, "text": text},
        timeout=10.0,
    )
