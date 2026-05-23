from __future__ import annotations

from datetime import datetime, timezone

from ploy_agent.sim.tracker import (
    build_tracker_payload,
    parse_planned_end,
    planned_end_for_run,
)


def test_parse_planned_end_from_notes() -> None:
    notes = "live paper trading; planned_end=2026-06-06T12:58:30.924395+00:00"
    dt = parse_planned_end(notes)
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 6


def test_planned_end_fallback_from_run_hours() -> None:
    started = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    end = planned_end_for_run(started, "live paper trading", run_hours=336.0)
    assert end is not None
    assert (end - started).total_seconds() == 336 * 3600


def test_build_tracker_forward_active() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
    started = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    payload = build_tracker_payload(
        run={
            "id": 4,
            "mode": "forward",
            "started_at": started,
            "ended_at": None,
            "notes": "live paper trading; planned_end=2026-06-06T12:00:00+00:00",
        },
        totals={
            "total_trades": 10,
            "open": 8,
            "closed": 2,
            "open_buys": 5,
            "open_sells": 3,
            "closed_buys": 1,
            "closed_sells": 1,
            "total_pnl_cents": 12.5,
            "opened_24h": 3,
            "closed_24h": 1,
        },
        recent_rows=[],
        now=now,
        sim_forward_run_hours=336.0,
    )
    assert payload["forward_active"] is True
    assert payload["current_run"]["id"] == 4
    assert payload["run_totals"]["open_buys"] == 5
    assert payload["current_run"]["hours_remaining"] is not None


def test_build_tracker_no_run() -> None:
    payload = build_tracker_payload(
        run=None,
        totals=None,
        recent_rows=[],
        now=datetime.now(timezone.utc),
        sim_forward_run_hours=336.0,
    )
    assert payload["forward_active"] is False
    assert payload["current_run"] is None
