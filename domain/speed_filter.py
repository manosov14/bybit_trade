# -*- coding: utf-8 -*-
"""
Speed filter module (ΔPrice / ATR(H1,14)) per spec:
- Compute SpeedRatio as |price_now - price_impulse_start| / ATR(H1, period)
- Impulse start is approximated as the extreme against the approach direction
  within the last K H1 bars (K from env: SPEED_LOOKBACK_H1, default 6).
- Filter is enabled by env flag SPEED_FILTER_ENABLED.
- Accept setup only if SPEED_ACCEPT_FROM <= SpeedRatio <= SPEED_ACCEPT_TO.
This keeps the logic simple and robust.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Literal, Sequence

import math

# We'll not import exchange/ccxt/ta here to keep domain clean:
# The caller passes precomputed H1 closes and ATR(H1, period) value.

@dataclass
class SpeedFilterParams:
    enabled: bool = True
    lookback_h1: int = 6
    accept_from: float = 1.0
    accept_to: float = 3.0

@dataclass
class SpeedFilterResult:
    passed: bool
    ratio: float
    impulse_start: float
    reason: str = ""

    @property
    def ok(self) -> bool:
        """Backward-compatible alias for `passed` used by older code."""
        return self.passed

def compute_impulse_start(h1_closes: Sequence[float], direction: Literal["up","down"], lookback: int) -> float:
    """
    Approximate impulse start as the extreme against the approach direction over last K H1 bars.
    - If price approaches a resistance from below (up), impulse start = recent swing low (min of last K).
    - If approaching a support from above (down), impulse start = recent swing high (max of last K).
    """
    if not h1_closes:
        raise ValueError("h1_closes is empty")
    lb = max(1, min(lookback, len(h1_closes)))
    window = list(h1_closes)[-lb:]
    if direction == "up":
        return min(window)
    else:
        return max(window)

def speed_ratio(price_now: float, impulse_start: float, atr_h1: float) -> float:
    if atr_h1 <= 0:
        return math.inf
    return abs(price_now - impulse_start) / atr_h1

def run_speed_filter(price_now: float,
                     h1_closes: Sequence[float],
                     atr_h1: float,
                     direction: Literal["up","down"],
                     params: SpeedFilterParams) -> SpeedFilterResult:
    if not params.enabled:
        return SpeedFilterResult(True, 0.0, price_now, reason="disabled")
    imp = compute_impulse_start(h1_closes, direction, params.lookback_h1)
    ratio = speed_ratio(price_now, imp, atr_h1)
    ok = (params.accept_from <= ratio <= params.accept_to)
    reason = "" if ok else f"ratio {ratio:.2f} outside [{params.accept_from},{params.accept_to}]"
    return SpeedFilterResult(ok, ratio, imp, reason=reason)