from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import asyncpg
import httpx


@dataclass
class StrategyContext:
    conn: asyncpg.Connection
    market_id: str
    mrow: asyncpg.Record
    mid: float
    game_state: dict[str, Any]
    model: dict[str, Any]
    http: httpx.AsyncClient
    depth_1c: float = 0.0
    spread: float | None = None


@dataclass
class StrategyResult:
    model_prob: float
    market_prob: float
    edge_cents: float
    confidence: float
    reasoning: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    signal_json: dict[str, Any] = field(default_factory=dict)
