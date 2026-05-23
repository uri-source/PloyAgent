from __future__ import annotations

import asyncio
import json
import signal

import httpx

from ploy_agent.common.adaptive_edge import adaptive_min_edge
from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.notifier import repo as rec_repo
from ploy_agent.notifier.rank import RankedPick, top_picks
from ploy_agent.notifier.slack import (
    SlackFeedEntry,
    reply_resolution,
    upsert_live_feed,
)
from ploy_agent.notifier.telegram import post_picks as tg_post_picks

log = get_logger("notifier")


def _pick_key(pick: RankedPick) -> tuple[str, str | None]:
    return pick.market_id, pick.strategy_id or None


async def _recently_notified_edges(
    pool, market_ids: list[str]
) -> dict[str, float]:
    """Return {market_id: last_edge_cents} for markets notified in the cooldown window."""
    if not market_ids:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (market_id) market_id, payload_json
            FROM recommendations
            WHERE market_id = ANY($1::text[])
              AND ts > NOW() - INTERVAL '15 minutes'
              AND status = 'pending'
            ORDER BY market_id, ts DESC
            """,
            market_ids,
        )
    out: dict[str, float] = {}
    for r in rows:
        pj = r["payload_json"] or {}
        if isinstance(pj, str):
            pj = json.loads(pj)
        out[str(r["market_id"])] = abs(float(pj.get("edge_cents", 0)))
    return out


async def _resolve_pnl(pool, http: httpx.AsyncClient) -> None:
    """Check approved recommendations whose markets have closed and compute P&L."""
    async with pool.acquire() as conn:
        unresolved = await conn.fetch(
            """
            SELECT r.id, r.market_id, r.payload_json, m.status AS market_status,
                   r.slack_channel, r.slack_ts
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
            market_prob = float(payload.get("market_prob", 0.5)

            is_buy = edge_cents > 0
            entry_price = market_prob

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

            if final_price > 0.9:
                outcome = 1
            elif final_price < 0.1:
                outcome = 0
            else:
                continue

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

            slack_ch = row.get("slack_channel")
            slack_ts = row.get("slack_ts")
            if slack_ch and slack_ts and settings.slack_bot_token:
                try:
                    await reply_resolution(
                        http,
                        slack_ch,
                        slack_ts,
                        rec_id=rec_id,
                        outcome=outcome,
                        pnl_cents=pnl,
                        edge_direction=edge_dir,
                    )
                except Exception as e:
                    log.warning("slack_reply_failed", rec_id=rec_id, error=str(e))


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
        if settings.slack_bot_token and settings.slack_channel:
            async with pool.acquire() as conn:
                existing_ref = await rec_repo.latest_slack_message_ref(
                    conn, settings.slack_channel
                )
            if existing_ref:
                await upsert_live_feed(http, [], existing_ref=existing_ref)
        return

    prev_edges = await _recently_notified_edges(pool, [p.market_id for p in picks])
    deduped: list[RankedPick] = []
    for p in picks:
        prev = prev_edges.get(p.market_id)
        if prev is None:
            deduped.append(p)
        elif abs(p.edge_cents) >= prev * 2.0 and abs(p.edge_cents) >= settings.min_edge_cents * 2:
            deduped.append(p)
            log.info(
                "re_alert_edge_doubled",
                market_id=p.market_id,
                prev_edge=round(prev, 1),
                new_edge=round(abs(p.edge_cents), 1),
            )
    picks = deduped[: settings.rank_top_n]

    async with pool.acquire() as conn:
        adaptive_edge = await adaptive_min_edge(conn)
    min_e = settings.alert_min_edge or adaptive_edge
    min_d = settings.alert_min_depth
    min_s = settings.alert_min_score
    if min_e > 0 or min_d > 0 or min_s > 0:
        before = len(picks)
        picks = [
            p
            for p in picks
            if abs(p.edge_cents) >= min_e and p.depth_1c >= min_d and p.score >= min_s
        ]
        dropped = before - len(picks)
        if dropped:
            log.info(
                "alert_filter_dropped",
                dropped=dropped,
                min_edge=min_e,
                min_depth=min_d,
                min_score=min_s,
            )

    if not picks:
        log.info("no_new_picks")
        return

    async with pool.acquire() as conn:
        existing_ref = None
        if settings.slack_bot_token and settings.slack_channel:
            existing_ref = await rec_repo.latest_slack_message_ref(conn, settings.slack_channel)
        recent_refs = await rec_repo.recent_recommendation_refs(
            conn, [p.market_id for p in picks]
        )
        feed_entries: list[SlackFeedEntry] = []
        pick_ids: list[tuple[RankedPick, int]] = []
        created = 0
        for p in picks:
            ref = recent_refs.get(_pick_key(p))
            if ref is None:
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
                created += 1
                feed_entries.append(SlackFeedEntry(pick=p, rec_id=rec_id))
            else:
                feed_entries.append(
                    SlackFeedEntry(pick=p, rec_id=ref.rec_id, status=ref.status)
                )
            pick_ids.append((p, feed_entries[-1].rec_id))

    if created:
        log.info("recommendations_persisted", n=created)
    else:
        log.info("recommendations_refreshed", n=len(feed_entries))

    if settings.slack_bot_token and settings.slack_channel:
        ref = await upsert_live_feed(http, feed_entries, existing_ref=existing_ref)
        if ref:
            channel, ts = ref
            async with pool.acquire() as conn:
                for entry in feed_entries:
                    await rec_repo.update_slack_refs(conn, entry.rec_id, channel, ts)

    if settings.telegram_bot_token and settings.telegram_chat_id and pick_ids:
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
                    await _resolve_pnl(pool, http)
                except Exception as e:
                    log.warning("pnl_resolve_failed", error=str(e))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5.0)
                except TimeoutError:
                    pass
    finally:
        await close_pool()


def main() -> None:
    stop = asyncio.Event()
    asyncio.run(_run(stop))


if __name__ == "__main__":
    main()
