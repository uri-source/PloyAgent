from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class TradeRecord:
    profile_id: str
    market_id: str
    strategy_id: str
    category: str
    question: str | None
    opened_at: datetime
    closed_at: datetime | None
    pnl_cents: float | None
    status: str
    model_prob: float
    market_prob: float
    edge_cents: float
    resolved_outcome: int | None
    direction: str = "buy"
    exit_price: float | None = None
    close_reason: str | None = None
    score: float = 0.0


def _brier(p: float, y: int) -> float:
    return (p - y) ** 2


def summarize_trades(trades: list[TradeRecord]) -> dict[str, Any]:
    closed = [t for t in trades if t.status == "closed" and t.pnl_cents is not None]
    n_closed = len(closed)
    total_pnl = sum(float(t.pnl_cents) for t in closed)
    wins = sum(1 for t in closed if float(t.pnl_cents) > 0)
    pnls = [float(t.pnl_cents) for t in closed]
    mean_pnl = total_pnl / n_closed if n_closed else 0.0
    std_pnl = 0.0
    if n_closed > 1:
        var = sum((p - mean_pnl) ** 2 for p in pnls) / (n_closed - 1)
        std_pnl = var**0.5
    sharpe_like = mean_pnl / std_pnl if std_pnl > 1e-9 else None

    brier_vals: list[float] = []
    for t in closed:
        if t.resolved_outcome is not None:
            brier_vals.append(_brier(t.model_prob, t.resolved_outcome))

    return {
        "trade_count": len(trades),
        "closed_count": n_closed,
        "open_count": sum(1 for t in trades if t.status == "open"),
        "total_pnl_cents": round(total_pnl, 2),
        "wins": wins,
        "win_rate": round(wins / n_closed, 4) if n_closed else None,
        "mean_pnl_cents": round(mean_pnl, 3),
        "sharpe_like": round(sharpe_like, 4) if sharpe_like is not None else None,
        "brier_model": round(sum(brier_vals) / len(brier_vals), 4) if brier_vals else None,
    }


def group_summary(
    trades: list[TradeRecord],
    key_fn: Any,
) -> list[dict[str, Any]]:
    groups: dict[str, list[TradeRecord]] = defaultdict(list)
    for t in trades:
        groups[str(key_fn(t))].append(t)
    out: list[dict[str, Any]] = []
    for key, items in groups.items():
        s = summarize_trades(items)
        out.append({"key": key, **s})
    out.sort(key=lambda x: float(x.get("total_pnl_cents") or 0), reverse=True)
    return out


def daily_cumulative_series(trades: list[TradeRecord]) -> list[dict[str, Any]]:
    closed = [
        t
        for t in trades
        if t.status == "closed" and t.pnl_cents is not None and t.closed_at is not None
    ]
    closed.sort(key=lambda t: t.closed_at or t.opened_at)
    by_day: dict[date, float] = defaultdict(float)
    for t in closed:
        d = (t.closed_at or t.opened_at).date()
        by_day[d] += float(t.pnl_cents)
    running = 0.0
    series: list[dict[str, Any]] = []
    for d in sorted(by_day.keys()):
        running += by_day[d]
        series.append({"date": d.isoformat(), "pnl_day": round(by_day[d], 2), "cumulative": round(running, 2)})
    return series


def best_fit_markets(
    trades: list[TradeRecord],
    *,
    min_trades: int = 5,
    min_win_rate: float = 0.52,
) -> list[dict[str, Any]]:
    by_market = group_summary(trades, lambda t: t.market_id)
    fits: list[dict[str, Any]] = []
    for row in by_market:
        if row["closed_count"] < min_trades:
            continue
        wr = row.get("win_rate")
        pnl = float(row.get("total_pnl_cents") or 0)
        if wr is not None and wr >= min_win_rate and pnl > 0:
            sample = next((t for t in trades if t.market_id == row["key"]), None)
            if sample is None:
                continue
            fits.append(
                {
                    "market_id": row["key"],
                    "question": sample.question,
                    "category": sample.category,
                    **row,
                }
            )
    return fits


def compare_profiles(all_trades: list[TradeRecord]) -> list[dict[str, Any]]:
    by_profile: dict[str, list[TradeRecord]] = defaultdict(list)
    for t in all_trades:
        by_profile[t.profile_id].append(t)
    ranked: list[dict[str, Any]] = []
    for pid, items in by_profile.items():
        s = summarize_trades(items)
        ranked.append({"profile_id": pid, **s})
    ranked.sort(key=lambda x: float(x.get("total_pnl_cents") or 0), reverse=True)
    return ranked


def trades_from_rows(rows: list[Any]) -> list[TradeRecord]:
    out: list[TradeRecord] = []
    for r in rows:
        out.append(
            TradeRecord(
                profile_id=str(r["profile_id"]),
                market_id=str(r["market_id"]),
                strategy_id=str(r["strategy_id"]),
                category=str(r.get("category") or "unknown"),
                question=r.get("question"),
                opened_at=r["opened_at"],
                closed_at=r.get("closed_at"),
                pnl_cents=float(r["pnl_cents"]) if r.get("pnl_cents") is not None else None,
                status=str(r["status"]),
                model_prob=float(r["model_prob"]),
                market_prob=float(r["entry_price"]),
                edge_cents=float(r["edge_cents"]),
                resolved_outcome=int(r["resolved_outcome"])
                if r.get("resolved_outcome") is not None
                else None,
                direction=str(r.get("direction") or "buy").lower(),
                exit_price=float(r["exit_price"]) if r.get("exit_price") is not None else None,
                close_reason=str(r["close_reason"]) if r.get("close_reason") else None,
                score=float(r.get("score") or 0),
            )
        )
    return out


def daily_performance(trades: list[TradeRecord]) -> list[dict[str, Any]]:
    """Per-calendar-day stats for closed trades (by closed_at) and opens (by opened_at)."""
    closed_by_day: dict[date, list[TradeRecord]] = defaultdict(list)
    opened_by_day: dict[date, int] = defaultdict(int)

    for t in trades:
        opened_by_day[t.opened_at.date()] += 1
        if t.status == "closed" and t.closed_at is not None and t.pnl_cents is not None:
            closed_by_day[t.closed_at.date()].append(t)

    all_days = sorted(set(opened_by_day.keys()) | set(closed_by_day.keys()))
    out: list[dict[str, Any]] = []
    cumulative = 0.0
    for d in all_days:
        closed = closed_by_day.get(d, [])
        day_pnl = sum(float(t.pnl_cents) for t in closed)
        wins = sum(1 for t in closed if float(t.pnl_cents) > 0)
        cumulative += day_pnl
        buys = sum(1 for t in closed if t.direction == "buy")
        sells = sum(1 for t in closed if t.direction == "sell")
        out.append(
            {
                "date": d.isoformat(),
                "opened": opened_by_day.get(d, 0),
                "closed": len(closed),
                "buys_closed": buys,
                "sells_closed": sells,
                "wins": wins,
                "losses": len(closed) - wins,
                "pnl_cents": round(day_pnl, 2),
                "cumulative_pnl_cents": round(cumulative, 2),
                "win_rate": round(wins / len(closed), 4) if closed else None,
            }
        )
    return out


def close_reason_breakdown(trades: list[TradeRecord]) -> list[dict[str, Any]]:
    """Aggregate closed trades by close_reason."""
    groups: dict[str, list[TradeRecord]] = defaultdict(list)
    for t in trades:
        if t.status != "closed" or t.pnl_cents is None:
            continue
        key = t.close_reason or "unknown"
        groups[key].append(t)

    out: list[dict[str, Any]] = []
    for reason, items in groups.items():
        pnls = [float(t.pnl_cents) for t in items]
        wins = sum(1 for p in pnls if p > 0)
        out.append(
            {
                "close_reason": reason,
                "count": len(items),
                "wins": wins,
                "win_rate": round(wins / len(items), 4) if items else None,
                "total_pnl_cents": round(sum(pnls), 2),
                "mean_pnl_cents": round(sum(pnls) / len(items), 3) if items else 0.0,
            }
        )
    out.sort(key=lambda x: int(x["count"]), reverse=True)
    return out


def heatmap_profile_category(trades: list[TradeRecord]) -> list[dict[str, Any]]:
    cells: dict[tuple[str, str], list[TradeRecord]] = defaultdict(list)
    for t in trades:
        cells[(t.profile_id, t.category or "unknown")].append(t)
    out: list[dict[str, Any]] = []
    for (pid, cat), items in cells.items():
        s = summarize_trades(items)
        out.append({"profile_id": pid, "category": cat, "total_pnl_cents": s["total_pnl_cents"]})
    return out
