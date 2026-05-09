from __future__ import annotations

import asyncpg


async def rebuild_market_links(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        """
        SELECT id, gamma_event_id FROM markets
        WHERE gamma_event_id IS NOT NULL AND gamma_event_id <> ''
          AND status IS DISTINCT FROM 'closed'
        """
    )
    from collections import defaultdict

    by_ev: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_ev[str(r["gamma_event_id"])].append(str(r["id"]))
    ev_ids = list(by_ev.keys())
    if ev_ids:
        await conn.execute("DELETE FROM market_links WHERE gamma_event_id = ANY($1::text[])", ev_ids)
    for evid, ids in by_ev.items():
        if len(ids) < 2:
            continue
        ids_sorted = sorted(ids)
        for i in range(len(ids_sorted)):
            for j in range(i + 1, len(ids_sorted)):
                a, b = ids_sorted[i], ids_sorted[j]
                await conn.execute(
                    """
                    INSERT INTO market_links (market_id_a, market_id_b, link_type, gamma_event_id)
                    VALUES ($1,$2,'same_event_arb',$3)
                    ON CONFLICT (market_id_a, market_id_b) DO UPDATE SET
                      link_type = EXCLUDED.link_type,
                      gamma_event_id = EXCLUDED.gamma_event_id
                    """,
                    a,
                    b,
                    evid,
                )
