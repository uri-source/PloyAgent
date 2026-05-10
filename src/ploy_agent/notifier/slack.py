from __future__ import annotations

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


def _pick_block(pick: RankedPick, rec_id: int) -> list[dict[str, Any]]:
    edge_dir = "BUY" if pick.edge_cents > 0 else "SELL"
    edge_abs = abs(pick.edge_cents)
    q = pick.question or pick.market_id

    header = {
        "type": "header",
        "text": {"type": "plain_text", "text": f"{edge_dir} signal: {q[:148]}"},
    }

    details = (
        f"*Edge:* {edge_abs:.1f}¢ ({edge_dir})  |  "
        f"*Model:* {pick.model_prob:.1%}  |  *Market:* {pick.market_prob:.1%}\n"
        f"*Confidence:* {pick.confidence:.0%}  |  "
        f"*Depth:* {pick.depth_1c:.0f}  |  "
        f"*Score:* {pick.score:.2f}  |  "
        f"*Strategy:* `{pick.strategy_id}`"
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

    actions = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Approve"},
                "style": "primary",
                "action_id": "rec_approve",
                "value": str(rec_id),
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Reject"},
                "style": "danger",
                "action_id": "rec_reject",
                "value": str(rec_id),
            },
        ],
    }

    divider = {"type": "divider"}

    return [header, detail_section, reasoning_section, actions, divider]


def build_message_blocks(picks: list[tuple[RankedPick, int]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":chart_with_upwards_trend: *Top {len(picks)} Polymarket Edges*",
            },
        },
        {"type": "divider"},
    ]
    for pick, rec_id in picks:
        blocks.extend(_pick_block(pick, rec_id))
    return blocks


async def post_picks(
    client: httpx.AsyncClient,
    picks: list[tuple[RankedPick, int]],
) -> list[tuple[int, str, str]]:
    if not settings.slack_bot_token or not settings.slack_channel:
        log.warning("slack_not_configured")
        return []

    blocks = build_message_blocks(picks)
    payload = {
        "channel": settings.slack_channel,
        "text": f"Top {len(picks)} Polymarket edges",
        "blocks": blocks,
    }
    r = await client.post(_SLACK_POST_URL, headers=_headers(), json=payload, timeout=15.0)
    data = r.json()
    if not data.get("ok"):
        log.warning("slack_post_failed", error=data.get("error"))
        return []

    channel = data["channel"]
    ts = data["ts"]
    log.info("slack_posted", channel=channel, ts=ts, n=len(picks))
    return [(rec_id, channel, ts) for _, rec_id in picks]


async def update_message_status(
    client: httpx.AsyncClient,
    channel: str,
    ts: str,
    rec_id: int,
    status: str,
    user: str,
) -> None:
    emoji = ":white_check_mark:" if status == "approved" else ":x:"
    payload = {
        "channel": channel,
        "ts": ts,
        "text": f"{emoji} Recommendation #{rec_id} {status} by <@{user}>",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{emoji} Recommendation *#{rec_id}* was *{status}* by <@{user}>",
                },
            },
        ],
    }
    r = await client.post(_SLACK_UPDATE_URL, headers=_headers(), json=payload, timeout=10.0)
    data = r.json()
    if not data.get("ok"):
        log.warning("slack_update_failed", error=data.get("error"), rec_id=rec_id)
