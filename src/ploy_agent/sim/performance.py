from __future__ import annotations

from typing import Any

from ploy_agent.sim.metrics import (
    TradeRecord,
    close_reason_breakdown,
    daily_performance,
    summarize_trades,
)

# Shown on /paper and returned by /api/sim/performance for operators.
EXIT_RULES: list[dict[str, str]] = [
    {
        "id": "resolution",
        "label": "Market resolution",
        "description": (
            "Market settled; P&L from binary outcome (YES=1 / NO=0) at entry price."
        ),
    },
    {
        "id": "signal_reverse",
        "label": "Reverse signal",
        "description": (
            "New fair-value tick flips direction (BUY→SELL or vice versa) and passes "
            "the profile entry gates; exit at current market mid (mark-to-market)."
        ),
    },
    {
        "id": "max_hold",
        "label": "Max hold time",
        "description": (
            "Position held longer than category max (NBA/MLB/NFL/NHL/WNBA: 24h; "
            "other categories: 7 days); exit at current mid."
        ),
    },
    {
        "id": "mark_to_market",
        "label": "Mark to market",
        "description": "Bulk close at end of sim run/replay using last known mids.",
    },
]


def build_performance_payload(
    trades: list[TradeRecord],
    *,
    profile_id: str | None,
    sim_run_id: int | None,
) -> dict[str, Any]:
    totals = summarize_trades(trades)
    daily = daily_performance(trades)
    today_pnl: float | None = None
    if daily:
        from datetime import date

        today = date.today().isoformat()
        for row in reversed(daily):
            if row["date"] == today:
                today_pnl = float(row["pnl_cents"])
                break

    open_trades = [t for t in trades if t.status == "open"]
    return {
        "profile_id": profile_id,
        "sim_run_id": sim_run_id,
        "exit_rules": EXIT_RULES,
        "totals": totals,
        "today_pnl_cents": today_pnl,
        "daily": daily[-30:],
        "by_close_reason": close_reason_breakdown(trades),
        "open_positions": len(open_trades),
        "open_buys": sum(1 for t in open_trades if t.direction == "buy"),
        "open_sells": sum(1 for t in open_trades if t.direction == "sell"),
    }
