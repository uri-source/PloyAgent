from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class SimProfile:
    id: str
    min_edge_cents: float
    min_confidence: float
    min_model_prob: float
    strategy_ids: tuple[str, ...] = ()
    max_open_per_market: int = 1
    cooldown_sec: int = 900


@dataclass(frozen=True)
class SimSignal:
    ts: datetime
    market_id: str
    strategy_id: str
    category: str
    question: str | None
    model_prob: float
    market_prob: float
    edge_cents: float
    confidence: float
    score: float = 0.0


@dataclass
class OpenPosition:
    trade_id: int | None
    profile_id: str
    market_id: str
    strategy_id: str
    category: str
    question: str | None
    direction: str
    entry_price: float
    opened_at: datetime
    model_prob: float
    confidence: float
    edge_cents: float
    score: float


@dataclass
class ClosedTrade:
    profile_id: str
    market_id: str
    strategy_id: str
    category: str
    question: str | None
    opened_at: datetime
    closed_at: datetime
    direction: str
    entry_price: float
    exit_price: float | None
    model_prob: float
    confidence: float
    edge_cents: float
    score: float
    resolved_outcome: int | None
    pnl_cents: float
    close_reason: str
    trade_id: int | None = None


@dataclass
class PortfolioState:
    open_by_key: dict[tuple[str, str], OpenPosition] = field(default_factory=dict)
    last_entry_at: dict[tuple[str, str], datetime] = field(default_factory=dict)
