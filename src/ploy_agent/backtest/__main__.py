from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from typing import Any

import asyncpg

from ploy_agent.common.config import settings
from ploy_agent.common.pnl import compute_pnl_cents, outcome_from_final_mid, trade_direction


def _brier(p: float, y: float) -> float:
    return (p - y) ** 2


async def _replay(conn: asyncpg.Connection, market_id: str) -> tuple[int, int]:
    rows = await conn.fetch(
        """
        SELECT ts, mid
        FROM prices
        WHERE market_id = $1 AND mid IS NOT NULL
        ORDER BY ts ASC
        """,
        market_id,
    )
    last: float | None = None
    triggers = 0
    for r in rows:
        mid = float(r["mid"])
        if last is None or abs(mid - last) >= 0.02:
            triggers += 1
        last = mid
    return len(rows), triggers


async def _full_backtest(conn: asyncpg.Connection, min_edge: float) -> None:
    """Backtest all resolved markets: Brier per strategy, calibration, P&L sim."""
    # Get resolved recommendations
    recs = await conn.fetch(
        """
        SELECT r.id, r.market_id, r.strategy_id, r.payload_json,
               r.resolved_outcome, r.pnl_cents, r.entry_price, r.edge_direction
        FROM recommendations r
        WHERE r.resolved_outcome IS NOT NULL
        ORDER BY r.resolved_at ASC
        """
    )
    if not recs:
        print("No resolved recommendations to backtest.")
        print("Falling back to fair_values backtest...")
        await _fair_values_backtest(conn, min_edge)
        return

    print(f"\n{'='*60}")
    print(f"  BACKTEST REPORT — {len(recs)} resolved recommendations")
    print(f"{'='*60}\n")

    strats: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in recs:
        pj = r["payload_json"] or {}
        if isinstance(pj, str):
            pj = json.loads(pj)
        strats[r["strategy_id"] or "unknown"].append({
            "model_prob": float(pj.get("model_prob", 0.5)),
            "market_prob": float(pj.get("market_prob", 0.5)),
            "edge_cents": float(pj.get("edge_cents", 0)),
            "outcome": int(r["resolved_outcome"]),
            "pnl_cents": float(r["pnl_cents"]) if r["pnl_cents"] is not None else 0,
        })

    total_pnl = 0.0
    total_n = 0
    total_wins = 0
    all_model_brier: list[float] = []
    all_market_brier: list[float] = []

    for sid, entries in sorted(strats.items()):
        n = len(entries)
        m_brier = [_brier(e["model_prob"], e["outcome"]) for e in entries]
        k_brier = [_brier(e["market_prob"], e["outcome"]) for e in entries]
        pnl = sum(e["pnl_cents"] for e in entries)
        wins = sum(1 for e in entries if e["pnl_cents"] > 0)
        correct = sum(
            1 for e in entries
            if (e["model_prob"] >= 0.5 and e["outcome"] == 1)
            or (e["model_prob"] < 0.5 and e["outcome"] == 0)
        )

        avg_m = sum(m_brier) / n
        avg_k = sum(k_brier) / n

        print(f"  Strategy: {sid}")
        print(f"    Resolved:       {n}")
        print(f"    Model Brier:    {avg_m:.4f}")
        print(f"    Market Brier:   {avg_k:.4f}")
        print(f"    Edge vs Market: {avg_k - avg_m:+.4f} {'(model better)' if avg_k > avg_m else '(market better)' if avg_m > avg_k else '(tied)'}")
        print(f"    Accuracy:       {correct}/{n} ({correct / n * 100:.1f}%)")
        print(f"    P&L:            {pnl:+.1f}¢")
        print(f"    Win Rate:       {wins}/{n} ({wins / n * 100:.1f}%)")
        print()

        total_pnl += pnl
        total_n += n
        total_wins += wins
        all_model_brier.extend(m_brier)
        all_market_brier.extend(k_brier)

    if total_n > 0:
        avg_total_m = sum(all_model_brier) / total_n
        avg_total_k = sum(all_market_brier) / total_n
        print(f"  {'─'*40}")
        print(f"  TOTAL ({total_n} resolved)")
        print(f"    Model Brier:    {avg_total_m:.4f}")
        print(f"    Market Brier:   {avg_total_k:.4f}")
        print(f"    Edge vs Market: {avg_total_k - avg_total_m:+.4f}")
        print(f"    Total P&L:      {total_pnl:+.1f}¢")
        print(f"    Win Rate:       {total_wins}/{total_n} ({total_wins / total_n * 100:.1f}%)")

    # Calibration buckets
    print(f"\n  CALIBRATION (model_prob buckets)")
    print(f"  {'Bucket':>12} {'N':>5} {'Avg Pred':>10} {'Actual':>10} {'Gap':>10}")
    all_entries = [e for entries in strats.values() for e in entries]
    buckets = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in buckets:
        in_bucket = [e for e in all_entries if lo <= e["model_prob"] < hi]
        if not in_bucket:
            continue
        avg_pred = sum(e["model_prob"] for e in in_bucket) / len(in_bucket)
        avg_actual = sum(e["outcome"] for e in in_bucket) / len(in_bucket)
        gap = avg_pred - avg_actual
        label = f"[{lo:.1f}-{hi:.1f})"
        print(f"  {label:>12} {len(in_bucket):>5} {avg_pred:>10.3f} {avg_actual:>10.3f} {gap:>+10.3f}")

    print(f"\n{'='*60}\n")


async def _fair_values_backtest(conn: asyncpg.Connection, min_edge: float) -> None:
    """Backtest using fair_values + final market resolution (price-based)."""
    # Get markets that have resolved (final mid > 0.9 or < 0.1)
    resolved = await conn.fetch(
        """
        WITH final_prices AS (
          SELECT DISTINCT ON (market_id) market_id, mid
          FROM prices
          WHERE mid IS NOT NULL
          ORDER BY market_id, ts DESC
        ),
        resolved AS (
          SELECT market_id,
                 CASE WHEN mid > 0.9 THEN 1 WHEN mid < 0.1 THEN 0 ELSE NULL END AS outcome
          FROM final_prices
        )
        SELECT r.market_id, r.outcome
        FROM resolved r
        JOIN markets m ON m.id = r.market_id
        WHERE r.outcome IS NOT NULL
          AND m.status = 'closed'
        """
    )
    if not resolved:
        print("No closed/resolved markets found in the database.")
        return

    outcome_map = {str(r["market_id"]): int(r["outcome"]) for r in resolved}

    # Get fair values for resolved markets
    fv_rows = await conn.fetch(
        """
        SELECT DISTINCT ON (market_id, strategy_id)
               market_id, strategy_id, model_prob, market_prob, edge_cents
        FROM fair_values
        WHERE market_id = ANY($1::text[])
        ORDER BY market_id, strategy_id, ts DESC
        """,
        list(outcome_map.keys()),
    )
    if not fv_rows:
        print("No fair_values for resolved markets.")
        return

    print(f"\n{'='*60}")
    print(f"  FAIR VALUES BACKTEST — {len(fv_rows)} predictions across {len(outcome_map)} markets")
    print(f"{'='*60}\n")

    strats: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in fv_rows:
        mid = str(r["market_id"])
        if mid not in outcome_map:
            continue
        outcome = outcome_map[mid]
        mp = float(r["model_prob"])
        kp = float(r["market_prob"])
        edge = float(r["edge_cents"])

        pnl = 0.0
        if abs(edge) >= min_edge:
            pnl = compute_pnl_cents(kp, trade_direction(edge), outcome)

        strats[r["strategy_id"] or "unknown"].append({
            "model_prob": mp,
            "market_prob": kp,
            "edge_cents": edge,
            "outcome": outcome,
            "pnl_cents": pnl,
            "traded": abs(edge) >= min_edge,
        })

    for sid, entries in sorted(strats.items()):
        n = len(entries)
        traded = [e for e in entries if e["traded"]]
        m_brier = sum(_brier(e["model_prob"], e["outcome"]) for e in entries) / n
        k_brier = sum(_brier(e["market_prob"], e["outcome"]) for e in entries) / n
        pnl = sum(e["pnl_cents"] for e in traded)
        wins = sum(1 for e in traded if e["pnl_cents"] > 0)

        print(f"  Strategy: {sid}")
        print(f"    Predictions:    {n}")
        print(f"    Model Brier:    {m_brier:.4f}")
        print(f"    Market Brier:   {k_brier:.4f}")
        print(f"    Edge vs Market: {k_brier - m_brier:+.4f}")
        if traded:
            print(f"    Simulated trades: {len(traded)} (min edge {min_edge}¢)")
            print(f"    Sim P&L:        {pnl:+.1f}¢")
            print(f"    Sim Win Rate:   {wins}/{len(traded)} ({wins / len(traded) * 100:.1f}%)")
        print()

    print(f"{'='*60}\n")


async def _amain(args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(args.database_url or settings.database_url)
    try:
        if args.market_id and not args.full:
            n, trig = await _replay(conn, args.market_id)
            print(f"prices_rows={n} triggers_2c={trig}")

        if args.full:
            await _full_backtest(conn, min_edge=args.min_edge)
        elif args.brier_yes_prob is not None:
            y = float(args.brier_yes_prob)
            rows = await conn.fetch(
                "SELECT model_prob FROM fair_values"
                + (" WHERE market_id = $1" if args.brier_market else "")
                + " ORDER BY ts ASC",
                *([args.brier_market] if args.brier_market else []),
            )
            if not rows:
                print("brier=na (no fair_values)")
            else:
                vals = [_brier(float(r["model_prob"]), y) for r in rows]
                print(f"brier_model_mean={sum(vals) / len(vals):.6f}")
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Backtest: replay stored data, compute Brier scores, simulate P&L."
    )
    p.add_argument("--database-url", default=None)
    p.add_argument("--market-id", default=None, help="Replay mid stream for trigger counts.")
    p.add_argument("--brier-yes-prob", default=None, help="Compute mean Brier vs this outcome (0-1).")
    p.add_argument("--brier-market", default=None, help="Limit Brier computation to a market_id.")
    p.add_argument(
        "--full", action="store_true",
        help="Full backtest: per-strategy Brier, calibration, P&L simulation.",
    )
    p.add_argument(
        "--min-edge", type=float, default=3.0,
        help="Min edge (cents) to simulate a trade (default: 3.0).",
    )
    args = p.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
