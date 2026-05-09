"""Apply SQL migrations in lexical order."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg

from ploy_agent.common.config import settings


def _split_sql(sql: str) -> list[str]:
    """Split migration file on statement boundaries (line ends with ';')."""
    statements: list[str] = []
    cur: list[str] = []
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            continue
        cur.append(line)
        if line.strip().endswith(";"):
            stmt = "\n".join(cur).strip()
            if stmt.endswith(";"):
                stmt = stmt[:-1].strip()
            if stmt:
                statements.append(stmt)
            cur = []
    tail = "\n".join(cur).strip()
    if tail:
        statements.append(tail.rstrip(";").strip())
    return [s for s in statements if s]


async def _apply() -> None:
    dsn = os.environ.get("DATABASE_URL") or settings.database_url
    conn = await asyncpg.connect(dsn)
    try:
        here = Path(__file__).resolve().parent / "migrations"
        files = sorted(here.glob("*.sql"))
        if not files:
            raise RuntimeError(f"No migrations in {here}")
        for f in files:
            raw = f.read_text()
            for stmt in _split_sql(raw):
                await conn.execute(stmt)
            print(f"applied: {f.name}")
    finally:
        await conn.close()


def main() -> None:
    asyncio.run(_apply())


if __name__ == "__main__":
    main()
