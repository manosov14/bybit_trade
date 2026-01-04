
from dataclasses import dataclass
import pandas as pd
import numpy as np

@dataclass
class DeltaParams:
    lookback_minutes: int = 60            # окно для перцентилей
    top_percentile: float = 95.0          # перцентиль кластер-объёма
    min_imbalance: float = 0.65           # ask:bid или bid:ask в сторону прорыва
    max_price_progress_pct: float = 0.2   # % от цены — "без прогресса"
    confirm_flip: bool = True             # нужен обратный дисбаланс на следующей свече

@dataclass
class DeltaResult:
    passed: bool
    reason: str
    stats: dict

def _delta_from_trades(trades: pd.DataFrame)->pd.Series:
    """Оцениваем дельту по сделкам: сумма qty по сторонам."""
    if trades.empty:
        return pd.Series(dtype='float64')
    if 'side' in trades.columns:
        buys = trades.loc[trades['side'].str.lower()=='buy','amount'].sum()
        sells = trades.loc[trades['side'].str.lower()=='sell','amount'].sum()
        return pd.Series({'buy': float(buys), 'sell': float(sells), 'delta': float(buys - sells)})
    # fallback: грубая оценка по направлению изменения тиков
    trades = trades.sort_values('timestamp')
    moves = trades['price'].diff().fillna(0.0)
    buys = trades.loc[moves>=0,'amount'].sum()
    sells = trades.loc[moves<0,'amount'].sum()
    return pd.Series({'buy': float(buys), 'sell': float(sells), 'delta': float(buys - sells)})

def evaluate_delta_absorption(m1: pd.DataFrame, level_price: float, kind: str, params: DeltaParams)->DeltaResult:
    """
    m1: OHLCV 1m с колонками ['ts','open','high','low','close','volume']
    Проверяем свип последней 1м свечи: всплеск объёма/дельты без прогресса и flip.
    """
    if len(m1)<params.lookback_minutes+5:
        return DeltaResult(False, "not_enough_history", {'need': params.lookback_minutes+5, 'have': len(m1)})
    m1 = m1.copy()
    m1['ret'] = m1['close'].pct_change().fillna(0.0)
    # кластер "свип" — последняя минута
    sweep = m1.iloc[-1]
    lookback = m1.iloc[-params.lookback_minutes-1:-1]
    # перцентиль объёма
    vol_pctl = (lookback['volume'].rank(pct=True) * 100.0).iloc[-1]  # позиция последнего объёма в ряду
    vol_is_top = sweep['volume'] >= np.percentile(lookback['volume'].values, params.top_percentile)
    # имбаланс (псевдо, без сделок): по направлению свечи
    if (sweep['close']>=sweep['open']):
        ask_ratio = 0.7; bid_ratio = 0.3
    else:
        ask_ratio = 0.3; bid_ratio = 0.7
    if kind=='PDH':  # пробой вверх
        imbalance_ok = ask_ratio >= params.min_imbalance
        # без прогресса: не ушли дальше X% от уровня
        progress = (sweep['high'] - level_price) / max(level_price, 1e-9) * 100.0
        no_progress = progress <= params.max_price_progress_pct
        # flip: следующая св. закрылась ниже уровня
        flip_ok = True  # проверим при наличии следующей
    else:            # пробой вниз
        imbalance_ok = bid_ratio >= params.min_imbalance
        progress = (level_price - sweep['low']) / max(level_price, 1e-9) * 100.0
        no_progress = progress <= params.max_price_progress_pct
        flip_ok = True

    # проверим flip на предыдущей минуте как прокси (в онлайне — на следующей)
    prev = m1.iloc[-2]
    if params.confirm_flip:
        if kind=='PDH':
            flip_ok = prev['close'] <= level_price
        else:
            flip_ok = prev['close'] >= level_price

    passed = bool(vol_is_top and imbalance_ok and no_progress and flip_ok)
    stats = {
        'vol_is_top': bool(vol_is_top),
        'vol_pctl': float(vol_pctl),
        'imbalance_ok': bool(imbalance_ok),
        'progress_pct': float(progress),
        'no_progress': bool(no_progress),
        'flip_ok': bool(flip_ok),
    }
    return DeltaResult(passed, "" if passed else "delta_absorption_failed", stats)
