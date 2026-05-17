from __future__ import annotations

from ploy_agent.common.config import Settings
from ploy_agent.common.logging_config import get_logger
from ploy_agent.strategies.base import Strategy
from ploy_agent.strategies.baseline_model import BaselineModelStrategy
from ploy_agent.strategies.behavior_fade import BehaviorFadeStrategy
from ploy_agent.strategies.book_imbalance import BookImbalanceStrategy
from ploy_agent.strategies.consensus import ConsensusStrategy
from ploy_agent.strategies.cross_market_arb import CrossMarketArbStrategy
from ploy_agent.strategies.player_adjust import PlayerAdjustStrategy
from ploy_agent.strategies.sportsbook_consensus import SportsbookConsensusStrategy
from ploy_agent.strategies.stale_quote import StaleQuoteStrategy

log = get_logger("strategies.registry")

STRATEGIES: dict[str, Strategy] = {
    BaselineModelStrategy.id: BaselineModelStrategy(),
    StaleQuoteStrategy.id: StaleQuoteStrategy(),
    SportsbookConsensusStrategy.id: SportsbookConsensusStrategy(),
    CrossMarketArbStrategy.id: CrossMarketArbStrategy(),
    BehaviorFadeStrategy.id: BehaviorFadeStrategy(),
    PlayerAdjustStrategy.id: PlayerAdjustStrategy(),
    BookImbalanceStrategy.id: BookImbalanceStrategy(),
    ConsensusStrategy.id: ConsensusStrategy(),
}


def get_enabled(settings: Settings) -> list[Strategy]:
    out: list[Strategy] = []
    for sid in settings.strategy_ids():
        strat = STRATEGIES.get(sid)
        if strat is None:
            log.warning("unknown_strategy_id", strategy_id=sid)
            continue
        req = strat.requires
        if "odds_api" in req and not settings.odds_api_key:
            log.warning("strategy_skipped_missing_odds_api", strategy_id=sid)
            continue
        out.append(strat)
    if not out:
        log.warning("no_strategies_enabled_fallback_baseline")
        out.append(STRATEGIES[BaselineModelStrategy.id])
    return out
