from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging
from ploy_agent.kalshi import client
from ploy_agent.kalshi.map_wc_games import map_wc_games
from ploy_agent.kalshi.pairs import load_pairs_yaml, validate_and_upsert_pairs


async def cmd_load_pairs(path: Path, *, strict: bool) -> None:
    specs = load_pairs_yaml(path)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            stats = await validate_and_upsert_pairs(conn, specs, strict=strict)
    finally:
        await close_pool()
    print(f"Loaded {stats['loaded']} pairs ({stats['skipped']} skipped)")


async def cmd_map_wc_games(*, dry_run: bool, min_confidence: float | None) -> None:
    pool = await get_pool()
    try:
        async with client.new_http_client() as http:
            async with pool.acquire() as conn:
                stats = await map_wc_games(
                    conn, http, dry_run=dry_run, min_confidence=min_confidence
                )
    finally:
        await close_pool()
    print(
        f"WC game map: active={stats.matched_active} review={stats.matched_review} "
        f"skipped={stats.skipped} (poly={stats.poly_candidates} kalshi={stats.kalshi_markets})"
    )


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description="Kalshi cross-venue utilities")
    p.add_argument("--database-url", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    lp = sub.add_parser("load-pairs", help="Load curated YAML into cross_venue_pairs")
    lp.add_argument("path", type=Path, help="YAML file path")
    lp.add_argument(
        "--strict",
        action="store_true",
        help="Skip pairs unless poly market and kalshi_markets row exist",
    )

    mg = sub.add_parser(
        "map-wc-games",
        help="Auto-map Polymarket fifwc-* moneylines to Kalshi KXWCGAME tickers",
    )
    mg.add_argument("--dry-run", action="store_true", help="Log candidates without writing DB")
    mg.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Auto-activate threshold (default: CROSS_VENUE_MAP_MIN_CONFIDENCE)",
    )

    args = p.parse_args()
    if args.command == "load-pairs":
        asyncio.run(cmd_load_pairs(args.path, strict=args.strict))
    elif args.command == "map-wc-games":
        asyncio.run(cmd_map_wc_games(dry_run=args.dry_run, min_confidence=args.min_confidence))
    else:
        p.error(f"unknown command {args.command}")


if __name__ == "__main__":
    main()
