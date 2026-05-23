from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

_PLANNED_END_RE = re.compile(r"planned_end=([^\s;]+)")


def parse_planned_end(notes: str | None) -> datetime | None:
    if not notes:
        return None
    m = _PLANNED_END_RE.search(notes)
    if not m:
        return None
    raw = m.group(1)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def planned_end_for_run(
    started_at: datetime,
    notes: str | None,
    *,
    run_hours: float,
) -> datetime | None:
    parsed = parse_planned_end(notes)
    if parsed is not None:
        return parsed
    if run_hours <= 0:
        return None
    started = started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started + timedelta(hours=run_hours)


def trade_row_to_dict(row: dict[str, Any] | Any) -> dict[str, Any]:
    """Serialize a sim_trades row for API / dashboard."""
    if hasattr(row, "keys"):
        r = dict(row)
    else:
        r = row
    edge = float(r["edge_cents"])
    return {
        "id": int(r["id"]),
        "sim_run_id": int(r["sim_run_id"]) if r.get("sim_run_id") is not None else None,
        "profile_id": str(r["profile_id"]),
        "market_id": str(r["market_id"]),
        "question": r.get("question") or "",
        "category": r.get("category") or "",
        "strategy_id": str(r["strategy_id"]),
        "opened_at": str(r["opened_at"]),
        "closed_at": str(r["closed_at"]) if r.get("closed_at") else None,
        "status": str(r["status"]),
        "direction": str(r["direction"]).upper(),
        "edge_cents": edge,
        "entry_price": float(r["entry_price"]),
        "exit_price": float(r["exit_price"]) if r.get("exit_price") is not None else None,
        "confidence": float(r["confidence"]),
        "model_prob": float(r["model_prob"]),
        "pnl_cents": float(r["pnl_cents"]) if r.get("pnl_cents") is not None else None,
        "close_reason": r.get("close_reason"),
        "event_at": str(r.get("event_at") or r["opened_at"]),
    }


def build_tracker_payload(
    *,
    run: dict[str, Any] | None,
    totals: dict[str, Any] | None,
    recent_rows: list[Any],
    now: datetime,
    sim_forward_run_hours: float,
) -> dict[str, Any]:
    if run is None:
        return {
            "forward_active": False,
            "current_run": None,
            "run_totals": None,
            "recent_trades": [],
            "config": {"sim_forward_run_hours": sim_forward_run_hours},
        }

    started_at = run["started_at"]
    if isinstance(started_at, datetime):
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
    else:
        started_at = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))

    ended_at = run.get("ended_at")
    forward_active = ended_at is None and str(run.get("mode")) == "forward"

    planned_end = planned_end_for_run(
        started_at,
        run.get("notes"),
        run_hours=sim_forward_run_hours,
    )
    hours_remaining: float | None = None
    if planned_end is not None and forward_active:
        if planned_end.tzinfo is None:
            planned_end = planned_end.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        hours_remaining = max(0.0, (planned_end - now).total_seconds() / 3600.0)

    elapsed_hours = max(0.0, (now - started_at).total_seconds() / 3600.0)

    current_run = {
        "id": int(run["id"]),
        "mode": str(run["mode"]),
        "started_at": str(run["started_at"]),
        "ended_at": str(ended_at) if ended_at else None,
        "notes": run.get("notes"),
        "planned_end": planned_end.isoformat() if planned_end else None,
        "hours_remaining": round(hours_remaining, 2) if hours_remaining is not None else None,
        "elapsed_hours": round(elapsed_hours, 2),
        "run_hours": sim_forward_run_hours if sim_forward_run_hours > 0 else None,
    }

    run_totals = None
    if totals:
        run_totals = {
            "total_trades": int(totals["total_trades"]),
            "open": int(totals["open"]),
            "closed": int(totals["closed"]),
            "open_buys": int(totals["open_buys"]),
            "open_sells": int(totals["open_sells"]),
            "closed_buys": int(totals["closed_buys"]),
            "closed_sells": int(totals["closed_sells"]),
            "total_pnl_cents": float(totals["total_pnl_cents"]),
            "opened_24h": int(totals["opened_24h"]),
            "closed_24h": int(totals["closed_24h"]),
        }

    return {
        "forward_active": forward_active,
        "current_run": current_run,
        "run_totals": run_totals,
        "recent_trades": [trade_row_to_dict(r) for r in recent_rows],
        "config": {"sim_forward_run_hours": sim_forward_run_hours},
    }
