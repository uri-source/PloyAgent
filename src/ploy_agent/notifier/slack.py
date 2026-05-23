from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from ploy_agent.common.config import settings
from ploy_agent.common.logging_config import get_logger
from ploy_agent.notifier.rank import RankedPick

log = get_logger("notifier.slack")

_SLACK_POST_URL = "https://slack.com/api/chat.postMessage"
_SLACK_UPDATE_URL = "https://slack.com/api/chat.update"
_SLACK_DELETE_URL = "https://slack.com/api/chat.delete"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.slack_bot_token}",
        "Content-Type": "application/json; charset=utf-8",
    }


@dataclass(frozen=True)
class SlackFeedEntry:
    pick: RankedPick
    rec_id: int
    status: str = "pending"


def _status_block(status: str, user: str | None = None) -> dict[str, Any]:
    status_norm = status.strip().lower()
    if status_norm == "approved":
        emoji, label = ":white_check_mark:", "Approved"
    elif status_norm == "rejected":
        emoji, label = ":x:", "Rejected"
    else:
        emoji, label = ":hourglass_flowing_sand:", status_norm.title() or "Pending"
    by = f" by <@{user}>" if user else ""
    return {
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"{emoji} *Status:* {label}{by}"},
        ],
    }


def _summary_text(entries: list[SlackFeedEntry]) -> str:
    now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    if not entries:
        return f":satellite: *Polyagent live feed* · no ranked edges right now · updated {now}"
    pending = sum(1 for e in entries if e.status == "pending")
    return (
        f":chart_with_upwards_trend: *Polyagent live feed* · "
        f"{len(entries)} tracked · {pending} pending · updated {now}"
    )


def _pick_block(entry: SlackFeedEntry) -> list[dict[str, Any]]:
    pick = entry.pick
    edge_dir = "BUY" if pick.edge_cents > 0 else "SELL"
    edge_abs = abs(pick.edge_cents)
    q = pick.question or pick.market_id

    header = {
        "type": "header",
        "text": {"type": "plain_text", "text": f"{edge_dir} signal: {q[:148]}"},
    }

    kelly_str = f"{pick.kelly_frac * 100:.1f}%" if pick.kelly_frac > 0 else "—"
    decay_str = f"{pick.decay:.0%}" if pick.decay < 0.99 else ""
    decay_note = f"  |  *Freshness:* {decay_str}" if decay_str else ""

    details = (
        f"*Edge:* {edge_abs:.1f}¢ ({edge_dir})  |  "
        f"*Model:* {pick.model_prob:.1%}  |  *Market:* {pick.market_prob:.1%}\n"
        f"*Confidence:* {pick.confidence:.0%}  |  "
        f"*Depth:* {pick.depth_1c:.0f}  |  "
        f"*Score:* {pick.score:.2f}  |  "
        f"*Kelly:* {kelly_str}  |  "
        f"*Strategy:* `{pick.strategy_id}`{decay_note}"
    )
    detail_section = {
        "type": "section",
        "text": {"type": "mrkdwn", "text": details},
    }

    reasoning_section = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"*Reasoning:* {pick.reasoning[:500]}" if pick.reasoning else "_No reasoning provided_",
        },
    }

    if entry.status == "pending":
        action_or_status = {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": "rec_approve",
                    "value": str(entry.rec_id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Reject"},
                    "style": "danger",
                    "action_id": "rec_reject",
                    "value": str(entry.rec_id),
                },
            ],
        }
    else:
        action_or_status = _status_block(entry.status)

    divider = {"type": "divider"}

    return [header, detail_section, reasoning_section, action_or_status, divider]


def build_message_blocks(entries: list[SlackFeedEntry]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _summary_text(entries),
            },
        },
        {"type": "divider"},
    ]
    if not entries:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_No ranked edges right now. Feed will refresh automatically when new picks appear._",
                },
            }
        )
        return blocks
    for entry in entries:
        blocks.extend(_pick_block(entry))
    return blocks


async def upsert_live_feed(
    client: httpx.AsyncClient,
    entries: list[SlackFeedEntry],
    *,
    existing_ref: tuple[str, str] | None = None,
) -> tuple[str, str] | None:
    if not settings.slack_bot_token or not settings.slack_channel:
        log.warning("slack_not_configured")
        return None

    blocks = build_message_blocks(entries)
    text = "Polyagent live feed"
    if existing_ref is None:
        payload = {
            "channel": settings.slack_channel,
            "text": text,
            "blocks": blocks,
        }
        url = _SLACK_POST_URL
    else:
        channel, ts = existing_ref
        payload = {
            "channel": channel,
            "ts": ts,
            "text": text,
            "blocks": blocks,
        }
        url = _SLACK_UPDATE_URL
    r = await client.post(url, headers=_headers(), json=payload, timeout=15.0)
    data = r.json()
    if not data.get("ok"):
        log.warning("slack_feed_upsert_failed", error=data.get("error"))
        return None

    channel = str(data["channel"])
    ts = str(data["ts"])
    log.info("slack_feed_upserted", channel=channel, ts=ts, n=len(entries))
    return channel, ts


def replace_action_block_with_status(
    blocks: list[dict[str, Any]] | None,
    rec_id: int,
    status: str,
    user: str,
) -> list[dict[str, Any]]:
    if not blocks:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Recommendation #{rec_id}* was *{status}* by <@{user}>",
                },
            }
        ]
    out: list[dict[str, Any]] = []
    target = str(rec_id)
    replaced = False
    for block in blocks:
        if not replaced and block.get("type") == "actions":
            elements = block.get("elements") or []
            if any(str(el.get("value") or "") == target for el in elements if isinstance(el, dict)):
                out.append(_status_block(status, user))
                replaced = True
                continue
        out.append(block)
    if not replaced:
        out.append(_status_block(status, user))
    return out


async def update_message_status(
    client: httpx.AsyncClient,
    channel: str,
    ts: str,
    rec_id: int,
    status: str,
    user: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
) -> None:
    emoji = ":white_check_mark:" if status == "approved" else ":x:"
    updated_blocks = replace_action_block_with_status(blocks, rec_id, status, user)
    payload = {
        "channel": channel,
        "ts": ts,
        "text": f"{emoji} Recommendation #{rec_id} {status} by <@{user}>",
        "blocks": updated_blocks,
    }
    r = await client.post(_SLACK_UPDATE_URL, headers=_headers(), json=payload, timeout=10.0)
    data = r.json()
    if not data.get("ok"):
        log.warning("slack_update_failed", error=data.get("error"), rec_id=rec_id)


async def reply_resolution(
    client: httpx.AsyncClient,
    channel: str,
    thread_ts: str,
    rec_id: int,
    outcome: int,
    pnl_cents: float,
    edge_direction: str,
) -> None:
    """Post a thread reply to the original Slack alert with resolution outcome + P&L."""
    if not settings.slack_bot_token:
        return

    outcome_str = "YES ✅" if outcome == 1 else "NO ❌"
    pnl_sign = "+" if pnl_cents >= 0 else ""
    result_emoji = "🟢" if pnl_cents >= 0 else "🔴"

    text = (
        f"{result_emoji} *Resolved* — Outcome: *{outcome_str}*\n"
        f"Direction: `{edge_direction.upper()}` → P&L: *{pnl_sign}{pnl_cents:.1f}¢*"
    )

    payload = {
        "channel": channel,
        "thread_ts": thread_ts,
        "text": text,
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": text},
            },
        ],
    }
    r = await client.post(_SLACK_POST_URL, headers=_headers(), json=payload, timeout=10.0)
    data = r.json()
    if not data.get("ok"):
        log.warning("slack_reply_failed", error=data.get("error"), rec_id=rec_id)
    else:
        log.info("slack_resolution_replied", rec_id=rec_id, pnl=pnl_cents)
