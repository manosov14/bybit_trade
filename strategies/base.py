from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Protocol

import pandas as pd


@dataclass(frozen=True)
class MarketSnapshot:
    """A bundle of candles per timeframe for a single symbol."""

    symbol: str
    tf: Dict[str, pd.DataFrame]  # e.g. {"1d": df, "4h": df, "1h": df, "5m": df, "1m": df}


@dataclass(frozen=True)
class StrategyContext:
    """Runtime context passed into strategies."""

    now_utc_iso: str
    env: dict


@dataclass(frozen=True)
class SignalIntent:
    """A pure intent produced by analysis (no exchange side effects)."""

    symbol: str
    side: str  # "buy" or "sell"
    entry: float | None
    sl: float | None
    tp: float | None
    ttl_sec: int | None = None
    tag: str | None = None
    reason: str | None = None
    meta: dict | None = None


class IStrategy(Protocol):
    def on_snapshot(self, snap: MarketSnapshot, ctx: StrategyContext) -> List[SignalIntent]:
        ...
