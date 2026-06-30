from __future__ import annotations

import argparse
import asyncio
import csv
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.sim import repo as sim_repo
from ploy_agent.sim.forward import run_forward
from ploy_agent.sim.metrics import (
    best_fit_markets,
    compare_profiles,
    daily_cumulative_series,
    group_summary,
    heatmap_profile_category,
    summarize_trades,
    trades_from_rows,
)
from ploy_agent.sim.profiles import default_profile_grid, high_conviction_profiles
from ploy_agent.sim.replay import run_replay

log = get_logger("sim")


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def cmd_init_profiles(conn: asyncpg.Connection, subset: bool) -> None:
    profiles = default_profile_grid(subset=subset) + high_conviction_profiles()
    for p in profiles:
        await sim_repo.upsert_profile(conn, p)
    print(f"Upserted {len(profiles)} simulation profiles.")


async def cmd_replay(
    conn: asyncpg.Connection,
    from_ts: datetime,
    to_ts: datetime,
    profile_ids: list[str] | None,
) -> None:
    profiles = await sim_repo.list_profiles(conn, profile_ids)
    if not profiles:
        print("No sim_profiles found. Run: ploy-sim init-profiles")
        return
    run_id = await run_replay(
        conn,
        from_ts=from_ts,
        to_ts=to_ts,
        profiles=profiles,
        notes=f"replay {from_ts.isoformat()} — {to_ts.isoformat()}",
    )
    rows = await sim_repo.fetch_trades(conn, sim_run_id=run_id, limit=100_000)
    trades = trades_from_rows(rows)
    summary = summarize_trades(trades)
    print(f"\nReplay run_id={run_id} complete.")
    print(f"  Trades written: {summary['trade_count']} (closed: {summary['closed_count']})")
    print(f"  Total P&L (all profiles): {summary['total_pnl_cents']:+.1f}¢")


async def cmd_report(
    conn: asyncpg.Connection,
    profile_id: str | None,
    group_by: str,
    export_dir: Path,
) -> None:
    rows = await sim_repo.fetch_trades(conn, profile_id=profile_id, limit=50_000)
    trades = trades_from_rows(rows)
    if not trades:
        print("No sim_trades found.")
        return

    if profile_id:
        print(f"\n=== Profile {profile_id} ===")
        print(summarize_trades(trades))
    else:
        print("\n=== All profiles ===")
        for row in compare_profiles(trades)[:10]:
            print(
                f"  {row['profile_id']}: P&L {row['total_pnl_cents']:+.1f}¢ "
                f"closed={row['closed_count']} win_rate={row.get('win_rate')}"
            )

    key_fn = {
        "market": lambda t: t.market_id,
        "category": lambda t: t.category,
        "strategy": lambda t: t.strategy_id,
    }.get(group_by, lambda t: t.category)

    grouped = group_summary(trades, key_fn)
    print(f"\n--- By {group_by} (top 10) ---")
    for g in grouped[:10]:
        print(
            f"  {g['key']}: P&L {g['total_pnl_cents']:+.1f}¢ "
            f"n={g['closed_count']} win_rate={g.get('win_rate')}"
        )

    fits = best_fit_markets(trades)
    if fits:
        print("\n--- Best-fit markets ---")
        for f in fits[:5]:
            print(f"  {f.get('question') or f['market_id']}: P&L {f['total_pnl_cents']:+.1f}¢")

    export_dir.mkdir(parents=True, exist_ok=True)
    series = daily_cumulative_series(trades)
    if series and profile_id:
        path = export_dir / f"series_{profile_id}.csv"
        with path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["date", "pnl_day", "cumulative"])
            w.writeheader()
            w.writerows(series)
        print(f"\nExported daily series to {path}")


async def cmd_compare(conn: asyncpg.Connection) -> None:
    rows = await sim_repo.fetch_trades(conn, limit=100_000)
    trades = trades_from_rows(rows)
    ranked = compare_profiles(trades)
    print(f"\n{'Profile':<22} {'P&L':>10} {'Closed':>8} {'Win%':>8} {'Sharpe':>8}")
    print("-" * 60)
    for row in ranked:
        wr = row.get("win_rate")
        wr_s = f"{wr * 100:.1f}" if wr is not None else "n/a"
        sh = row.get("sharpe_like")
        sh_s = f"{sh:.3f}" if sh is not None else "n/a"
        print(
            f"{row['profile_id']:<22} {row['total_pnl_cents']:>+9.1f}¢ "
            f"{row['closed_count']:>8} {wr_s:>7} {sh_s:>8}"
        )
    heat = heatmap_profile_category(trades)
    if heat:
        print("\nTop profile×category cells:")
        heat.sort(key=lambda x: float(x["total_pnl_cents"]), reverse=True)
        for h in heat[:8]:
            print(f"  {h['profile_id']} / {h['category']}: {h['total_pnl_cents']:+.1f}¢")


async def _amain(args: argparse.Namespace) -> None:
    configure_logging()
    if args.command == "forward":
        pool = await get_pool()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def _shutdown() -> None:
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _shutdown)
            except (NotImplementedError, AttributeError):
                pass
        try:
            profile_csv = args.profiles or settings.sim_forward_profiles or None
            async with pool.acquire() as conn:
                profiles = await sim_repo.list_profiles(
                    conn, profile_csv.split(",") if profile_csv else None
                )
            if not profiles:
                print("No profiles. Run: ploy-sim init-profiles")
                return
            run_hours = (
                settings.sim_forward_run_hours
                if args.hours is None
                else args.hours
            )
            await run_forward(pool, profiles, stop, run_hours=run_hours)
        finally:
            await close_pool()
        return

    conn = await asyncpg.connect(args.database_url or settings.database_url)
    try:
        if args.command == "init-profiles":
            await cmd_init_profiles(conn, subset=args.subset)
        elif args.command == "replay":
            replay_days = args.days if args.days is not None else settings.sim_replay_days
            to_ts = _parse_dt(args.to) if args.to else datetime.now(timezone.utc)
            from_ts = (
                _parse_dt(args.from_ts)
                if args.from_ts
                else to_ts - timedelta(days=replay_days)
            )
            pids = args.profiles.split(",") if args.profiles else None
            await cmd_replay(conn, from_ts, to_ts, pids)
        elif args.command == "report":
            await cmd_report(
                conn,
                profile_id=args.profile,
                group_by=args.by,
                export_dir=Path(args.export_dir),
            )
        elif args.command == "compare":
            await cmd_compare(conn)
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Paper-trading simulation across threshold profiles.")
    p.add_argument("--database-url", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    ip = sub.add_parser("init-profiles", help="Seed default edge×confidence×model_prob grid.")
    ip.add_argument("--subset", action="store_true", help="Smaller grid for quick tests.")

    rp = sub.add_parser("replay", help="Historical replay over fair_values.")
    rp.add_argument("--from", dest="from_ts", default=None, help="ISO start time (UTC).")
    rp.add_argument("--to", default=None, help="ISO end time (UTC).")
    rp.add_argument(
        "--days",
        type=int,
        default=None,
        help="Lookback days if --from omitted (default: SIM_REPLAY_DAYS).",
    )
    rp.add_argument("--profiles", default=None, help="Comma-separated profile ids.")

    fp = sub.add_parser("forward", help="Live paper trading loop.")
    fp.add_argument("--profiles", default=None, help="Comma-separated profile ids (default: all).")
    fp.add_argument(
        "--hours",
        type=float,
        default=None,
        help="Run length in hours (default: SIM_FORWARD_RUN_HOURS; 0 = unlimited).",
    )

    rep = sub.add_parser("report", help="Summary and optional CSV export.")
    rep.add_argument("--profile", default=None)
    rep.add_argument("--by", choices=("market", "category", "strategy"), default="category")
    rep.add_argument("--export-dir", default="artifacts/sim")

    sub.add_parser("compare", help="Rank all profiles by simulated P&L.")

    args = p.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
