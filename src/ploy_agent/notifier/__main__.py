from __future__ import annotations

import asyncio
import json
import signal

import httpx

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.notifier import repo as rec_repo
from ploy_agent.notifier.rank import RankedPick, top_picks
from ploy_agent.notifier.slack import post_picks as slack_post_picks
from ploy_agent.notifier.telegram import post_picks as tg_post_picks

log = get_logger("notifier")


async def _recently_notified(pool, market_ids: list[str]) -> set[str]:
    if not market_ids:
        return set()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT market_id FROM recommendations
            WHERE market_id = ANY($1::text[])
              AND ts > NOW() - INTERVAL '15 minutes'
              AND status = 'pending'
            """,
            market_ids,
        )
    return {str(r["market_id"]) for r in rows}


async def _resolve_pnl(pool) -> None:
    """Check approved recommendations whose markets have closed and compute P&L."""
    async with pool.acquire() as conn:
        unresolved = await conn.fetch(
            """
            SELECT r.id, r.market_id, r.payload_json, m.status AS market_status
            FROM recommendations r
            JOIN markets m ON m.id = r.market_id
            WHERE r.status = 'approved'
              AND r.resolved_outcome IS NULL
              AND m.status = 'closed'
            LIMIT 50
            """
        )
        if not unresolved:
            return

        for row in unresolved:
            rec_id = row["id"]
            payload = row["payload_json"] or {}
            if isinstance(payload, str):
                payload = json.loads(payload)

            edge_cents = float(payload.get("edge_cents", 0))
            market_prob = float(payload.get("market_prob", 0.5))

            # BUY if edge > 0 (model thinks YES is underpriced), SELL if edge < 0
            is_buy = edge_cents > 0
            entry_price = market_prob

            # Check final price to determine resolution
            final_price = await conn.fetchval(
                """
                SELECT mid FROM prices
                WHERE market_id = $1 AND mid IS NOT NULL
                ORDER BY ts DESC LIMIT 1
                """,
                row["market_id"],
            )

            if final_price is None:
                continue

            # Determine outcome: final price > 0.9 → YES; < 0.1 → NO
            if final_price > 0.9:
                outcome = 1
            elif final_price < 0.1:
                outcome = 0
            else:
                continue  # Ambiguous — skip

            # Calculate P&L in cents
            if is_buy:
                pnl = ((1.0 - entry_price) * 100.0) if outcome == 1 else (-entry_price * 100.0)
            else:
                pnl = (entry_price * 100.0) if outcome == 0 else (-(1.0 - entry_price) * 100.0)

            edge_dir = "buy" if is_buy else "sell"

            await conn.execute(
                """
                UPDATE recommendations
                SET resolved_outcome = $2, pnl_cents = $3, resolved_at = NOW(),
                    entry_price = $4, edge_direction = $5
                WHERE id = $1
                """,
                rec_id,
                outcome,
                pnl,
                entry_price,
                edge_dir,
            )
            log.info(
                "pnl_resolved",
                rec_id=rec_id,
                outcome=outcome,
                pnl_cents=round(pnl, 2),
                direction=edge_dir,
            )


async def _tick(pool, http: httpx.AsyncClient) -> None:
    async with pool.acquire() as conn:
        picks = await top_picks(
            conn,
            limit=settings.rank_top_n * 2,
            strategy_ids=settings.strategy_ids(),
            merge_by_market=settings.rank_merge_by_market,
        )
    if not picks:
        log.info("no_picks")
        return

    already = await _recently_notified(pool, [p.market_id for p in picks])
    picks = [p for p in picks if p.market_id not in already][: settings.rank_top_n]
    if not picks:
        log.info("no_new_picks")
        return

    pick_ids: list[tuple[RankedPick, int]] = []
    async with pool.acquire() as conn:
        for p in picks:
            payload = {
                "strategy_id": p.strategy_id,
                "edge_cents": p.edge_cents,
                "model_prob": p.model_prob,
                "market_prob": p.market_prob,
                "confidence": p.confidence,
                "reasoning": p.reasoning,
            }
            rec_id = await rec_repo.insert_recommendation(
                conn,
                market_id=p.market_id,
                score=p.score,
                payload=payload,
                strategy_id=p.strategy_id,
            )
            pick_ids.append((p, rec_id))
    log.info("recommendations_persisted", n=len(pick_ids))

    # Post to Slack
    if settings.slack_bot_token and settings.slack_channel:
        refs = await slack_post_picks(http, pick_ids)
        if refs:
            async with pool.acquire() as conn:
                for rec_id, channel, ts in refs:
                    await rec_repo.update_slack_refs(conn, rec_id, channel, ts)

    # Post to Telegram
    if settings.telegram_bot_token and settings.telegram_chat_id:
        tg_refs = await tg_post_picks(http, pick_ids)
        if tg_refs:
            async with pool.acquire() as conn:
                for rec_id, msg_id in tg_refs:
                    await rec_repo.update_telegram_refs(
                        conn, rec_id, settings.telegram_chat_id, msg_id
                    )


async def _run(stop: asyncio.Event) -> None:
    configure_logging()
    pool = await get_pool()
    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except (NotImplementedError, AttributeError):
            pass

    try:
        async with httpx.AsyncClient() as http:
            while not stop.is_set():
                try:
                    await _tick(pool, http)
                except Exception as e:
                    log.warning("notifier_tick_failed", error=str(e))
                try:
                    await _resolve_pnl(pool)
                except Exception as e:
                    log.warning("pnl_resolve_failed", error=str(e))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=60.0)
                except TimeoutError:
                    pass
    finally:
        await close_pool()


def main() -> None:
    stop = asyncio.Event()
    asyncio.run(_run(stop))


if __name__ == "__main__":
    main()
