"""One-shot backfill: classify market_type for existing recommendations."""
import asyncio
import re

import asyncpg


def classify(q):
    if not q:
        return "other"
    if re.search(r"O/U\s+\d", q, re.I):
        return "over_under"
    if re.search(r"over[/ ]under", q, re.I):
        return "over_under"
    if re.search(r"spread[:\s]", q, re.I):
        return "spread"
    if re.search(r"\(\s*[+-]?\d+\.5\s*\)", q, re.I):
        return "spread"
    if re.search(r"\bvs\.?\s+", q, re.I):
        return "winner"
    return "other"


async def main():
    conn = await asyncpg.connect("postgresql://postgres:postgres@localhost:5433/ploy_agent")
    rows = await conn.fetch("SELECT id, question FROM recommendations WHERE market_type IS NULL")
    for r in rows:
        mt = classify(r["question"])
        await conn.execute("UPDATE recommendations SET market_type = $1 WHERE id = $2", mt, r["id"])
    print(f"Updated {len(rows)} rows")
    await conn.close()


asyncio.run(main())
