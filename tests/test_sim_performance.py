from __future__ import annotations

from datetime import datetime, timezone

from ploy_agent.sim.metrics import TradeRecord, close_reason_breakdown, daily_performance
from ploy_agent.sim.performance import EXIT_RULES, build_performance_payload


def _trade(
    *,
    status: str = "closed",
    pnl: float = 5.0,
    closed_at: datetime | None = None,
    close_reason: str = "resolution",
    direction: str = "buy",
) -> TradeRecord:
    opened = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    closed = closed_at or datetime(2026, 5, 20, 18, 0, tzinfo=timezone.utc)
    return TradeRecord(
        profile_id="e5_c65_m60",
        market_id="m1",
        strategy_id="baseline_model",
        category="nba",
        question="Test?",
        opened_at=opened,
        closed_at=closed if status == "closed" else None,
        pnl_cents=pnl if status == "closed" else None,
        status=status,
        model_prob=0.6,
        market_prob=0.5,
        edge_cents=10.0,
        resolved_outcome=1 if close_reason == "resolution" else None,
        direction=direction,
        close_reason=close_reason if status == "closed" else None,
    )


def test_daily_performance_aggregates_closed_pnl():
    trades = [
        _trade(pnl=10.0, closed_at=datetime(2026, 5, 21, 1, 0, tzinfo=timezone.utc)),
        _trade(pnl=-3.0, closed_at=datetime(2026, 5, 21, 2, 0, tzinfo=timezone.utc)),
    ]
    daily = daily_performance(trades)
    by_date = {d["date"]: d for d in daily}
    assert by_date["2026-05-20"]["opened"] == 2
    assert by_date["2026-05-21"]["closed"] == 2
    assert by_date["2026-05-21"]["pnl_cents"] == 7.0
    assert by_date["2026-05-21"]["wins"] == 1


def test_close_reason_breakdown():
    trades = [
        _trade(close_reason="resolution", pnl=5.0),
        _trade(close_reason="signal_reverse", pnl=-2.0),
        _trade(close_reason="resolution", pnl=3.0),
    ]
    rows = close_reason_breakdown(trades)
    by_reason = {r["close_reason"]: r for r in rows}
    assert by_reason["resolution"]["count"] == 2
    assert by_reason["resolution"]["total_pnl_cents"] == 8.0
    assert by_reason["signal_reverse"]["count"] == 1


def test_build_performance_payload_includes_exit_rules():
    payload = build_performance_payload([_trade()], profile_id="e5_c65_m60", sim_run_id=1)
    assert len(payload["exit_rules"]) == len(EXIT_RULES)
    assert payload["totals"]["closed_count"] == 1
    assert payload["by_close_reason"][0]["close_reason"] == "resolution"
