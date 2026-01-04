from dataclasses import dataclass
import math
import pandas as pd

@dataclass
class RiskParams:
    account_equity: float
    risk_pct: float = 1.0  # percent per trade
    rr: float = 2.0        # take profit RR (TP = entry + rr*(entry-stop) in favorable direction)
    leverage: float = 3.0  # for position size computation (simplified)

@dataclass
class RiskResult:
    qty: float
    sl: float
    tp: float

def position_sizing(entry: float, stop: float, direction: str, rp: RiskParams)->RiskResult:
    risk_amount = rp.account_equity * rp.risk_pct/100.0
    per_unit_loss = abs(entry-stop)
    qty = risk_amount / per_unit_loss if per_unit_loss>0 else 0.0
    if direction=='LONG':
        tp = entry + rp.rr * (entry - stop)
    else:
        tp = entry - rp.rr * (stop - entry)
    return RiskResult(qty=qty, sl=stop, tp=tp)
