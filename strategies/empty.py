"""Empty strategy placeholder.

Use this as a template for your new logic.
"""

from __future__ import annotations

from typing import List

from strategies.base import IStrategy, MarketSnapshot, SignalIntent, StrategyContext


class Strategy(IStrategy):
    """Does nothing — produces no intents."""

    def on_snapshot(self, snap: MarketSnapshot, ctx: StrategyContext) -> List[SignalIntent]:
        return []
