from __future__ import annotations

import argparse
import asyncio
import asyncpg

from ploy_agent.common.config import settings


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


async def _brier_over_fairs(conn: asyncpg.Connection, market_id: str | None, outcome_yes: float) -> float | None:
    if market_id:
        rows = await conn.fetch(
            """
            SELECT model_prob FROM fair_values
            WHERE market_id = $1
            ORDER BY ts ASC
            """,
            market_id,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT model_prob FROM fair_values
            ORDER BY ts ASC
            """
        )
    if not rows:
        return None
    vals = [_brier(float(r["model_prob"]), outcome_yes) for r in rows]
    return sum(vals) / len(vals)


async def _amain(args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(args.database_url or settings.database_url)
    try:
        if args.market_id:
            n, trig = await _replay(conn, args.market_id)
            print(f"prices_rows={n} triggers_2c={trig}")
        if args.brier_yes_prob is not None:
            y = float(args.brier_yes_prob)
            b = await _brier_over_fairs(conn, args.brier_market, y)
            if b is None:
                print("brier=na (no fair_values)")
            else:
                print(f"brier_model_mean={b:.6f}")
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Replay stored prices / stub Brier on fair_values.")
    p.add_argument("--database-url", default=None)
    p.add_argument("--market-id", default=None, help="Replay mid stream for trigger counts.")
    p.add_argument("--brier-yes-prob", default=None, help="If set, compute mean Brier vs this outcome (0-1).")
    p.add_argument("--brier-market", default=None, help="Limit Brier computation to a market_id.")
    args = p.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
