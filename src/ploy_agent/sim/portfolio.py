from __future__ import annotations

from datetime import datetime, timedelta

from ploy_agent.common.config import settings
from ploy_agent.common.pnl import compute_pnl_cents, mtm_pnl_cents, trade_direction
from ploy_agent.common.scoring import passes_entry_price_gate, passes_risk_reward_gate
from ploy_agent.sim.rules import is_reverse_signal, should_enter
from ploy_agent.sim.types import ClosedTrade, OpenPosition, PortfolioState, SimProfile, SimSignal

# Max hold by market category slug (seconds)
MAX_HOLD_SEC: dict[str, int] = {
    "nba": 86400,
    "mlb": 86400,
    "nfl": 86400,
    "nhl": 86400,
    "wnba": 86400,
}
DEFAULT_MAX_HOLD_SEC = 7 * 86400


def max_hold_for_category(category: str) -> timedelta:
    sec = MAX_HOLD_SEC.get(category.lower(), DEFAULT_MAX_HOLD_SEC)
    return timedelta(seconds=sec)


def _pos_key(profile_id: str, market_id: str) -> tuple[str, str]:
    return profile_id, market_id


class ProfilePortfolio:
    def __init__(self, profile: SimProfile) -> None:
        self.profile = profile
        self.state = PortfolioState()

    def try_enter(self, signal: SimSignal) -> OpenPosition | None:
        if not should_enter(signal, self.profile):
            return None
        # Phase 1 guardrails: entry price cap + risk-reward gate
        if not passes_entry_price_gate(
            signal.market_prob, settings.entry_price_min, settings.entry_price_max
        ):
            return None
        if not passes_risk_reward_gate(
            signal.market_prob, signal.edge_cents, settings.min_risk_reward
        ):
            return None
        key = _pos_key(self.profile.id, signal.market_id)
        if key in self.state.open_by_key:
            return None
        last = self.state.last_entry_at.get(key)
        if last is not None:
            elapsed = (signal.ts - last).total_seconds()
            if elapsed < self.profile.cooldown_sec:
                return None
        direction = trade_direction(signal.edge_cents)
        pos = OpenPosition(
            trade_id=None,
            profile_id=self.profile.id,
            market_id=signal.market_id,
            strategy_id=signal.strategy_id,
            category=signal.category,
            question=signal.question,
            direction=direction,
            entry_price=signal.market_prob,
            opened_at=signal.ts,
            model_prob=signal.model_prob,
            confidence=signal.confidence,
            edge_cents=signal.edge_cents,
            score=signal.score,
        )
        self.state.open_by_key[key] = pos
        self.state.last_entry_at[key] = signal.ts
        return pos

    def close_position(
        self,
        market_id: str,
        closed_at: datetime,
        *,
        close_reason: str,
        exit_price: float | None = None,
        resolved_outcome: int | None = None,
        pnl_cents: float | None = None,
    ) -> ClosedTrade | None:
        key = _pos_key(self.profile.id, market_id)
        pos = self.state.open_by_key.pop(key, None)
        if pos is None:
            return None
        if pnl_cents is None and resolved_outcome is not None:
            pnl_cents = compute_pnl_cents(pos.entry_price, pos.direction, resolved_outcome)
        elif pnl_cents is None and exit_price is not None:
            pnl_cents = mtm_pnl_cents(pos.entry_price, exit_price, pos.direction)
        elif pnl_cents is None:
            pnl_cents = 0.0
        return ClosedTrade(
            trade_id=pos.trade_id,
            profile_id=pos.profile_id,
            market_id=pos.market_id,
            strategy_id=pos.strategy_id,
            category=pos.category,
            question=pos.question,
            opened_at=pos.opened_at,
            closed_at=closed_at,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            model_prob=pos.model_prob,
            confidence=pos.confidence,
            edge_cents=pos.edge_cents,
            score=pos.score,
            resolved_outcome=resolved_outcome,
            pnl_cents=pnl_cents,
            close_reason=close_reason,
        )

    def process_signal(
        self,
        signal: SimSignal,
        *,
        market_resolved: bool = False,
        resolved_outcome: int | None = None,
    ) -> list[ClosedTrade]:
        """Apply one fair-value tick; return any closes."""
        closed: list[ClosedTrade] = []
        key = _pos_key(self.profile.id, signal.market_id)
        pos = self.state.open_by_key.get(key)

        if pos is not None:
            if market_resolved and resolved_outcome is not None:
                ct = self.close_position(
                    signal.market_id,
                    signal.ts,
                    close_reason="resolution",
                    exit_price=signal.market_prob,
                    resolved_outcome=resolved_outcome,
                )
                if ct:
                    closed.append(ct)
                return closed

            if is_reverse_signal(signal, pos.direction) and should_enter(signal, self.profile):
                ct = self.close_position(
                    signal.market_id,
                    signal.ts,
                    close_reason="signal_reverse",
                    exit_price=signal.market_prob,
                )
                if ct:
                    closed.append(ct)

            elif (signal.ts - pos.opened_at) >= max_hold_for_category(pos.category):
                ct = self.close_position(
                    signal.market_id,
                    signal.ts,
                    close_reason="max_hold",
                    exit_price=signal.market_prob,
                )
                if ct:
                    closed.append(ct)

        self.try_enter(signal)
        return closed

    def close_all_open(
        self,
        at: datetime,
        *,
        close_reason: str = "mark_to_market",
        exit_prices: dict[str, float] | None = None,
    ) -> list[ClosedTrade]:
        closed: list[ClosedTrade] = []
        for key in list(self.state.open_by_key.keys()):
            _, market_id = key
            exit_p = (exit_prices or {}).get(market_id)
            ct = self.close_position(
                market_id,
                at,
                close_reason=close_reason,
                exit_price=exit_p,
            )
            if ct:
                closed.append(ct)
        return closed
