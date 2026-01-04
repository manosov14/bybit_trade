from __future__ import annotations
from dataclasses import dataclass
from typing import Literal, Optional, Dict, Any, List

@dataclass(frozen=True)
class Level:
    id: str
    symbol: str
    price: float
    kind: Literal["PDH","PDL","H4","MANUAL"]
    scope: Literal["D1","H4"]

@dataclass(frozen=True)
class SweepEvent:
    symbol: str
    level_id: str
    level_price: float
    side: Literal["long","short"]
    extreme_price: float
    depth_abs: float
    depth_atr_h1: float
    m1_bar_ts: int

@dataclass(frozen=True)
class FilterResult:
    accepted: bool
    reasons: List[str]
    extras: Dict[str, Any]

@dataclass(frozen=True)
class PlanRequest:
    symbol: str
    side: Literal["long","short"]
    entry_trigger: float
    stop_loss: float
    take_profit: float
    qty: float
    tags: Dict[str, Any]
