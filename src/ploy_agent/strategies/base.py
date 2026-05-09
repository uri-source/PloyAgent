from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ploy_agent.strategies.types import StrategyContext, StrategyResult


class Strategy(ABC):
    id: ClassVar[str]
    requires: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    async def run(self, ctx: StrategyContext) -> StrategyResult | None:
        raise NotImplementedError
