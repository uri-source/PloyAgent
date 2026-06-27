from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging
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

    args = p.parse_args()
    if args.command == "load-pairs":
        asyncio.run(cmd_load_pairs(args.path, strict=args.strict))
    else:
        p.error(f"unknown command {args.command}")


if __name__ == "__main__":
    main()
