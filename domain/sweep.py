"""
Sweep/false-break module: depth in ATR, close-back within N bars.
"""
from dataclasses import dataclass
import pandas as pd
from indicators.ta import atr

@dataclass
class SweepParams:
    atr_len: int = 14
    min_atr_frac: float = 0.15
    max_atr_frac: float = 0.35
    max_closeback_bars: int = 5   # on M5 (configurable)
    timeframe_minutes: int = 5    # if data is M5

@dataclass
class SweepResult:
    is_sweep: bool
    pierce_pct_atr: float = 0.0
    entry_ts: pd.Timestamp = None

def check_sweep(df: pd.DataFrame, level_price: float, direction: str, params: SweepParams)->SweepResult:
    """
    df: DataFrame with ['ts','open','high','low','close'] on intraday timeframe (e.g., M5).
    direction: 'LONG' expects sweep of lows (price dips below level and returns), 'SHORT' expects sweep of highs.
    """
    d = df.copy().reset_index(drop=True)
    d['atr'] = atr(d, params.atr_len)
    last_i = len(d)-1
    # detect penetration on the most recent bar
    lo, hi, close = d.loc[last_i, 'low'], d.loc[last_i,'high'], d.loc[last_i,'close']
    pierced = (direction=='LONG' and lo<level_price) or (direction=='SHORT' and hi>level_price)
    if not pierced:
        return SweepResult(False, 0.0, None)
    # depth in ATR
    depth = (level_price - lo) if direction=='LONG' else (hi - level_price)
    a = d.loc[last_i,'atr'] or 1e-8
    frac = float(depth)/float(a)
    if not (params.min_atr_frac <= frac <= params.max_atr_frac):
        return SweepResult(False, frac, None)
    # close back within N bars
    # i.e., close after penetration returns to the range side
    for j in range(max(0, last_i-params.max_closeback_bars), last_i+1):
        c = d.loc[j,'close']
        if (direction=='LONG' and c>level_price) or (direction=='SHORT' and c<level_price):
            return SweepResult(True, frac, d.loc[j,'ts'])
    return SweepResult(False, frac, None)
