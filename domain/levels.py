from typing import List, Literal, Optional
import pandas as pd
from datetime import datetime, timezone
from .levels_engine import Level

LevelKind = Literal['PDH','PDL']

class LevelsParams:
    def __init__(self, days_window: int = 10,
                 include_inside: bool = True,
                 include_inside_days: Optional[bool] = None,
                 **kwargs) -> None:
        if include_inside_days is None:
            include_inside_days = include_inside
        self.days_window = int(days_window)
        self.include_inside = bool(include_inside_days)
        self.include_inside_days = bool(include_inside_days)

def _ensure(df: pd.DataFrame) -> pd.DataFrame:
    cols = {"ts","open","high","low","close"}
    miss = cols - set(df.columns)
    if miss:
        raise ValueError(f"D1 dataframe missing columns: {sorted(miss)}")
    out = df.copy().reset_index(drop=True)
    if not pd.api.types.is_datetime64_any_dtype(out['ts']):
        try:
            out['ts'] = pd.to_datetime(out['ts'], unit='s', utc=True)
        except Exception:
            out['ts'] = pd.to_datetime(out['ts'], utc=True, errors='coerce')
    return out

def _later_bars_breaks(df: pd.DataFrame, idx: int, kind: LevelKind, price: float, ignore_swept_after_ts=None) -> bool:
    if idx+1 >= len(df):
        return False
    later = df.iloc[idx+1:]
    if ignore_swept_after_ts is not None:
        try:
            later = later[later['ts'] < ignore_swept_after_ts]
        except Exception:
            pass
    if kind == 'PDL':
        return (later['low'] <= price).any()
    else:
        return (later['high'] >= price).any()

def _cutoff_ts(last_closed_ts: pd.Timestamp, days_window: int) -> pd.Timestamp:
    try:
        return last_closed_ts - pd.Timedelta(days=int(days_window))
    except Exception:
        return last_closed_ts

def working_levels_d1(d1: pd.DataFrame, side: str, params: Optional[LevelsParams] = None,
                      ignore_swept_after_ts: Optional[pd.Timestamp] = None) -> List[Level]:
    params = params or LevelsParams()
    df = _ensure(d1)
    levels: List[Level] = []
    prefer: LevelKind = 'PDL' if str(side).upper() == 'LONG' else 'PDH'
    last_closed_ts = df.iloc[-2]['ts'] if len(df) >= 2 else df.iloc[-1]['ts']
    cutoff = _cutoff_ts(last_closed_ts, params.days_window)
    for idx in range(len(df)-2, -1, -1):
        row = df.iloc[idx]
        ts = row['ts']
        if ts < cutoff:
            break
        price = float(row['low'] if prefer == 'PDL' else row['high'])
        if _later_bars_breaks(df, idx, prefer, price, ignore_swept_after_ts=ignore_swept_after_ts):
            continue
        levels.append(Level(ts=ts, kind=prefer, price=price))
        if len(levels) >= int(params.days_window):
            break
    levels.sort(key=lambda L: L.ts)
    return levels

def working_levels_d1_for_retro(d1: pd.DataFrame, side: str, params: Optional[LevelsParams] = None) -> List[Level]:
    df = _ensure(d1)
    now_ts = df.iloc[-1]['ts']
    try:
        today_start = pd.Timestamp(now_ts.date(), tz=timezone.utc)
    except Exception:
        from datetime import datetime as _dt
        today_start = pd.Timestamp(_dt.utcnow().date(), tz=timezone.utc)
    return working_levels_d1(d1, side, params=params, ignore_swept_after_ts=today_start)
