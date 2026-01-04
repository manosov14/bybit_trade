
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List
from datetime import datetime, timezone
import pandas as pd

from infra.env import load_env, as_int, as_float
from domain.trend import d1_trend, h4_trend
from domain import sessions as sess
from domain.speed_filter import run_speed_filter, SpeedFilterParams
from indicators.ta import atr
from .contracts import SweepEvent, Level, FilterResult

@dataclass
class FilterPipelineAdapter:
    env_path: str = ".env"

    def run(self, ev: SweepEvent, level: Level, d1_df: pd.DataFrame, h4_df: pd.DataFrame, h1_df: pd.DataFrame, ignore_speed: bool=False) -> FilterResult:
        env = load_env(self.env_path)
        reasons: List[str] = []
        extras: Dict[str, Any] = {}

        # --- Trend rules ---
        level_kind = str(getattr(level, 'kind', '')).upper()
        level_scope = str(getattr(level, 'scope', '')).upper()
        side = str(getattr(ev, 'side', '')).upper()  # LONG -> покупка после ложного пробоя ниже уровня; SHORT -> продажа

        # Direction required by side
        required = 'LONG' if side == 'LONG' else 'SHORT'

        d1_flag = str(env.get('D1', env.get('ENABLE_D1', env.get('USE_D1_TREND', 'true')))).lower() in ('1','true','yes','on','y')
        h4_flag = str(env.get('H4', env.get('ENABLE_H4', env.get('USE_H4_TREND', 'false')))).lower() in ('1','true','yes','on','y')

        d1_dir = None
        h4_dir = None
        try:
            d1_dir = d1_trend(d1_df, as_int(env.get('D1_SMA_LEN'), 200))
        except Exception:
            pass
        try:
            h4_dir = h4_trend(h4_df, as_int(env.get('H4_SMA_LEN'), 50))
        except Exception:
            pass

        trend_ok = True
        # D1 уровни: строго по направлению D1 и только если D1 включён
        if level_scope == 'D1' and level_kind != 'MANUAL':
            trend_ok = d1_flag and (str(d1_dir).upper() == required if d1_dir else True)
        # H4/MANUAL уровни: не ограничиваем трендом (согласно ТЗ)
        else:
            trend_ok = True

        extras['trend'] = {'d1_flag': d1_flag, 'h4_flag': h4_flag, 'd1_dir': d1_dir, 'h4_dir': h4_dir, 'required': required}
        if not trend_ok:
            reasons.append('trend:disallowed')

        
        # --- Depth filter (min/max sweep ATR on H1) ---
        depth_ok = True
        depth_info: Dict[str, Any] = {}
        try:
            depth_val = getattr(ev, 'depth_atr_h1', None)
            min_frac = as_float(env.get('MIN_SWEEP_ATR_H1'), 0.0)
            max_frac = as_float(env.get('MAX_SWEEP_ATR_H1'), 1e9)
            depth_info = {'value': depth_val, 'min': min_frac, 'max': max_frac}
            if depth_val is not None:
                try:
                    dv = float(depth_val)
                    if dv < min_frac:
                        depth_ok = False
                        depth_info['ok'] = False
                        depth_info['reason'] = 'too_shallow'
                        reasons.append(f'depth:too_shallow({dv:.4f}<{min_frac:.4f})')
                    elif dv > max_frac:
                        depth_ok = False
                        depth_info['ok'] = False
                        depth_info['reason'] = 'too_deep'
                        reasons.append(f'depth:too_deep({dv:.4f}>{max_frac:.4f})')
                    else:
                        depth_info['ok'] = True
                except Exception:
                    # если не удалось интерпретировать глубину, считаем фильтр пройденным
                    depth_info['error'] = 'depth_cast_failed'
            else:
                depth_info['note'] = 'no_depth_value'
        except Exception as e:
            depth_ok = False
            depth_info = {'error': str(e)}
            reasons.append(f'depth_err:{e}')
        extras['depth'] = depth_info

# --- Session filter ---
        sessions_ok = True
        if str(env.get('USE_SESSIONS','false')).lower() in ('1','true','yes','on','y'):
            allowed = [s.strip().upper() for s in str(env.get('SESSIONS','EU,US')).split(',') if s.strip()]
            tz_now = datetime.now(timezone.utc)
            sessions_ok = sess.is_session_allowed(tz_now, allowed)
            extras['sessions'] = {'allowed': allowed, 'ok': sessions_ok}
            if not sessions_ok:
                reasons.append('session:closed')

        # --- Speed filter ---
        speed_ok = True
        speed_info: Dict[str, Any] = {}
        if not ignore_speed and str(env.get('USE_SPEED','false')).lower() in ('1','true','yes','on','y'):
            # ATR(H1)
            h1 = h1_df.copy()
            h1['atr'] = atr(h1, as_int(env.get('ATR_H1_LEN'), 14))
            atr_h1 = float(h1['atr'].iloc[-1] or 0.0) if len(h1) else 0.0
            lookback = as_int(env.get('SPEED_LOOKBACK_H1'), 6)
            acc_from = as_float(env.get('SPEED_ACCEPT_FROM', env.get('SPEED_MIN')), 0.8)
            acc_to   = as_float(env.get('SPEED_ACCEPT_TO', env.get('SPEED_MAX')), 2.0)
            direction = 'up' if side == 'SHORT' else 'down'
            try:
                params = SpeedFilterParams(
                    enabled=True,
                    lookback_h1=lookback,
                    accept_from=acc_from,
                    accept_to=acc_to,
                )
                closes = list(pd.to_numeric(h1['close'], errors='coerce').ffill().bfill().values)
                price_now = float(pd.to_numeric(h1['close'].iloc[-1], errors='coerce'))
                res = run_speed_filter(
                    price_now=price_now,
                    h1_closes=closes,
                    atr_h1=atr_h1,
                    direction=direction,
                    params=params,
                )
                speed_ok = bool(getattr(res, 'ok', getattr(res, 'passed', False)))
                speed_info = {
                    'ok': speed_ok,
                    'ratio': getattr(res, 'ratio', None),
                    'impulse_start': getattr(res, 'impulse_start', None),
                    'reason': getattr(res, 'reason', ''),
                    'accept_from': acc_from,
                    'accept_to': acc_to,
                }
                if not speed_ok:
                    reasons.append('speed:rejected')
            except Exception as e:
                speed_ok = False
                speed_info = {'error': str(e)}
                reasons.append(f'speed_err:{e}')
        elif ignore_speed:
            speed_info['ignored'] = True
        extras['speed'] = speed_info

        accepted = (trend_ok and depth_ok and sessions_ok and speed_ok)
        return FilterResult(accepted=accepted, reasons=reasons, extras=extras)