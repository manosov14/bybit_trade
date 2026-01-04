import pandas as pd
from dataclasses import dataclass
from domain.sweep import check_sweep, SweepParams
from domain.speed_filter import fast_approach_ok
from domain.delta import DeltaParams, evaluate_delta_absorption

@dataclass
class EvalConfig:
    half_atr_factor: float = 0.5
    max_speed_minutes: int = 30
    use_delta: bool = False

def evaluate_one_setup(m_df: pd.DataFrame, h1_df: pd.DataFrame, level_price: float, direction: str, cfg: EvalConfig):
    # 1) speed filter
    # Make an ATR on H1 proxy for fast approach; if m_df is M5 or M1, pass their ATR if available
    from indicators.ta import atr
    h1_df = h1_df.copy()
    h1_df['atr'] = atr(h1_df, 14)
    speed_ok = fast_approach_ok(h1_df, level_price, h1_df['atr'], cfg.half_atr_factor, cfg.max_speed_minutes)
    if not speed_ok:
        return {'ok': False, 'reason':'speed_filter'}
    # 2) sweep
    sw = check_sweep(m_df, level_price, direction, SweepParams())
    if not sw.is_sweep:
        return {'ok': False, 'reason':'sweep_invalid', 'pierce_pct_atr': sw.pierce_pct_atr}
    # 3) delta absorption (optional)
    if cfg.use_delta:
        dr = evaluate_delta_absorption(m_df, level_price, 'PDL' if direction=='LONG' else 'PDH', DeltaParams())
        if not dr.passed:
            return {'ok': False, 'reason':'delta_fail', 'delta': dr}
    return {'ok': True, 'entry_ts': sw.entry_ts, 'pierce_pct_atr': sw.pierce_pct_atr}
