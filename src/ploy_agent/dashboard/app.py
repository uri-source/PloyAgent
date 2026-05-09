from __future__ import annotations

import asyncio
import os

import asyncpg
import streamlit as st

from ploy_agent.common.config import settings


async def _stats(dsn: str) -> dict[str, float | int | None]:
    conn = await asyncpg.connect(dsn)
    try:
        n_m = await conn.fetchval("SELECT COUNT(*) FROM markets")
        n_p = await conn.fetchval("SELECT COUNT(*) FROM prices")
        n_g = await conn.fetchval("SELECT COUNT(*) FROM game_state")
        n_f = await conn.fetchval("SELECT COUNT(*) FROM fair_values")
        n_r = await conn.fetchval("SELECT COUNT(*) FROM recommendations")
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM recommendations WHERE status = 'pending'"
        )
        approved = await conn.fetchval(
            "SELECT COUNT(*) FROM recommendations WHERE status = 'approved'"
        )
        rejected = await conn.fetchval(
            "SELECT COUNT(*) FROM recommendations WHERE status = 'rejected'"
        )
        brier = await conn.fetchval(
            """
            SELECT AVG(POWER(model_prob - market_prob, 2))
            FROM (
              SELECT DISTINCT ON (market_id) market_id, model_prob, market_prob
              FROM fair_values
              ORDER BY market_id, ts DESC
            ) x
            """
        )
        return {
            "markets": int(n_m or 0),
            "prices": int(n_p or 0),
            "game_state": int(n_g or 0),
            "fair_values": int(n_f or 0),
            "recommendations": int(n_r or 0),
            "pending": int(pending or 0),
            "approved": int(approved or 0),
            "rejected": int(rejected or 0),
            "brier_vs_market": float(brier) if brier is not None else None,
        }
    finally:
        await conn.close()


def main() -> None:
    st.set_page_config(page_title="Polymarket Edge calibration", layout="wide")
    st.title("Polymarket Edge Agent — calibration (v0)")
    st.info("For live rankings and traces, run **`ploy-web`** and open http://127.0.0.1:8765")
    dsn = os.environ.get("DATABASE_URL") or settings.database_url
    stats = asyncio.run(_stats(dsn))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Markets", stats["markets"])
    c2.metric("Price rows", stats["prices"])
    c3.metric("Fair value rows", stats["fair_values"])
    c4.metric("Recommendations", stats["recommendations"])
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Pending", stats["pending"])
    c6.metric("Approved", stats["approved"])
    c7.metric("Rejected", stats["rejected"])
    c8.metric("Game state rows", stats["game_state"])
    st.caption(
        "Brier vs market column shows mean squared error between latest model_prob and "
        "latest market_prob per market (proxy until resolved outcomes are wired)."
    )
    st.metric(
        "Latest snapshot Brier (model vs market mid)",
        f"{stats['brier_vs_market']:.4f}" if stats["brier_vs_market"] is not None else "n/a",
    )


if __name__ == "__main__":
    main()
