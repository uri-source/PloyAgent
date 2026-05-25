from __future__ import annotations

import asyncio
import signal

import httpx

from ploy_agent.common.config import settings
from ploy_agent.common.db import close_pool, get_pool
from ploy_agent.common.logging_config import configure_logging, get_logger
from ploy_agent.common.ssl_utils import httpx_verify
from ploy_agent.enrichment import repo as erepo
from ploy_agent.enrichment.espn_summary import fetch_roster_names
from ploy_agent.enrichment.mapping import match_market_to_game
from ploy_agent.enrichment.repo import insert_game_state, upsert_market_game_map
from ploy_agent.enrichment.sports import get_provider
from ploy_agent.reasoning import repo as rrepo

log = get_logger("enrichment")


async def _tick(pool, client: httpx.AsyncClient) -> None:
    provider = get_provider()
    games = await provider.fetch_live_games(client)
    if not games:
        log.debug("no_live_games")
        return
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, question FROM markets WHERE status IS DISTINCT FROM 'closed'"
        )

        seen_games: set[str] = set()
        for r in rows:
            mid = str(r["id"])
            q = r.get("question")
            gid = match_market_to_game(q, games)
            if not gid:
                continue
            game = next((g for g in games if g.game_id == gid), None)
            if not game:
                continue

            prev = await erepo.fetch_latest_scores(conn, gid)
            await upsert_market_game_map(conn, mid, gid)
            h_score = game.home_score if game.home_score is not None else 0
            a_score = game.away_score if game.away_score is not None else 0
            await insert_game_state(
                conn,
                game_id=gid,
                home_score=h_score,
                away_score=a_score,
                period=game.period,
                time_remaining=game.time_remaining,
                possession=game.possession,
                home_team=game.home_team,
                away_team=game.away_team,
            )
            if prev:
                ph, pa = prev
                dh = abs(h_score - ph)
                da = abs(a_score - pa)
                if max(dh, da) >= settings.stale_quote_score_swing:
                    await rrepo.insert_game_event(
                        conn,
                        game_id=gid,
                        event_type="score_swing",
                        payload_json={
                            "prev_home": ph,
                            "prev_away": pa,
                            "delta_home": dh,
                            "delta_away": da,
                            "new_home": h_score,
                            "new_away": a_score,
                        },
                        home_score=h_score,
                        away_score=a_score,
                    )
                    log.info("game_event_score_swing", game_id=gid, dh=dh, da=da)

            if gid not in seen_games:
                seen_games.add(gid)
                hn, an = await fetch_roster_names(client, gid, league_key=game.espn_summary_league_key())
                if hn or an:
                    await erepo.insert_game_lineups(conn, game_id=gid, home_active=hn, away_active=an)


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
        async with httpx.AsyncClient(verify=httpx_verify()) as client:
            while not stop.is_set():
                try:
                    await _tick(pool, client)
                except Exception as e:
                    log.warning("enrichment_tick_failed", error=str(e))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=10.0)
                except TimeoutError:
                    pass
    finally:
        await close_pool()


def main() -> None:
    stop = asyncio.Event()
    asyncio.run(_run(stop))


if __name__ == "__main__":
    main()
