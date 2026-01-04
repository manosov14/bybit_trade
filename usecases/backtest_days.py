
from typing import Optional
from datetime import timedelta

from infra.env import load_env
import infra.exchange_ccxt as exch
import domain.levels as levels_mod
import domain.sweep as sweep_mod
import domain.risk as risk_mod

_fetch_ohlcv = getattr(exch, "fetch_ohlcv", None)
_price_tick  = getattr(exch, "price_tick", None)

def _ohlcv(ex, symbol:str, tf:str, limit:int=500):
    if callable(_fetch_ohlcv):
        try: return _fetch_ohlcv(ex, symbol, tf, limit=limit)
        except TypeError: return _fetch_ohlcv(symbol, tf, limit=limit)
        except Exception: pass
    try:
        import ccxt
        ex = ccxt.bybit({'enableRateLimit': True})
        return ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    except Exception as e:
        raise ImportError("fetch_ohlcv is not available") from e

def _tick(ex, symbol:str)->float:
    if callable(_price_tick):
        try: return float(_price_tick(ex, symbol))
        except TypeError: return float(_price_tick(symbol))
        except Exception: pass
    try:
        import ccxt
        ex = ccxt.bybit({'enableRateLimit': True})
        ex.load_markets()
        prec = ex.markets.get(symbol, {}).get('precision', {}).get('price', None)
        if isinstance(prec, int): return 10**(-prec)
        return 0.01
    except Exception:
        return 0.01

get_levels_for_symbol = getattr(levels_mod, "get_levels_for_symbol", getattr(levels_mod, "get_levels", lambda *_: []))
check_sweep = getattr(sweep_mod, "check_sweep", lambda **k: dict(speed_ok=True, depth_ok=True, depth_atr=0.2, closeback_ok=True, closeback_bars=1))
calc_rr_targets = getattr(risk_mod, "calc_rr_targets", lambda entry, stop, rr: (entry + (abs(entry-stop)*rr if entry>stop else -abs(entry-stop)*rr), rr))

def _pint(v, d): 
    try:
        s = str(v).strip().lower()
        if s in ("true","on","yes"): return 1
        if s in ("false","off","no"): return 0
        return int(s)
    except: return d
def _pfloat(v, d):
    try: return float(str(v).replace(",", ".").strip())
    except: return d

def backtest_days(env_path: Optional[str]=None, days: int=5)->bool:
    env = load_env(env_path)
    symbols = [s.strip() for s in env.get("SYMBOLS","").split(",") if s.strip()]
    rr = _pfloat(env.get("RR","3"), 3.0)
    entry_ticks = _pint(env.get("ENTRY_TICKS","3"), 3)
    stop_ticks  = _pint(env.get("STOP_TICKS","3"), 3)
    stop_mode   = env.get("STOP_MODE","spike_or_ticks")
    min_atr_frac = _pfloat(env.get("SWEEP_MIN_ATR_FRAC",0.15),0.15)
    max_atr_frac = _pfloat(env.get("SWEEP_MAX_ATR_FRAC",0.35),0.35)

    print(f"Бэктест последних {days} дней по {len(symbols)} инструментам")
    for symbol in symbols:
        try:
            m5 = _ohlcv(None, symbol, "5m", limit=days*12*24 + 200)
            if not m5: 
                print(f"{symbol}: нет данных")
                continue
            import pandas as pd
            df = pd.DataFrame(m5, columns=["ts","open","high","low","close","volume"])
            df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
            tick = _tick(None, symbol)
            lvls = get_levels_for_symbol(symbol)

            wins=0; losses=0; armed=0; canceled=0
            for L in lvls:
                price = getattr(L, "price", None)
                side  = getattr(L, "side", "LONG").upper()
                if price is None: continue

                start_ts = df['ts'].iloc[-1] - timedelta(days=days)
                dff = df[df['ts']>=start_ts].copy()
                if dff.empty: continue

                is_armed=False; order_entry=None; stop=None; tp=None
                for i in range(2, len(dff)):
                    row = dff.iloc[i]; prev = dff.iloc[i-1]
                    pierced = (row.low < price) if side=="LONG" else (row.high > price)
                    if not pierced: continue
                    sw = check_sweep(symbol=symbol, df=dff.iloc[:i+1], level=price, direction=("down" if side=="LONG" else "up"))
                    depth = float(sw.get("depth_atr",0.0))

                    if depth > max_atr_frac:
                        if is_armed:
                            canceled+=1
                        is_armed=False; order_entry=None; break

                    if (not is_armed) and depth >= min_atr_frac and sw.get("speed_ok", True):
                        order_entry = price + entry_ticks*tick if side=="LONG" else price - entry_ticks*tick
                        stop_spike = min(row.low, prev.low) if side=="LONG" else max(row.high, prev.high)
                        stop_ticks_price = price - stop_ticks*tick if side=="LONG" else price + stop_ticks*tick
                        stop = stop_spike if stop_mode=="spike_or_ticks" else stop_ticks_price
                        tp,_ = calc_rr_targets(order_entry, stop, rr)
                        is_armed=True; armed+=1
                        continue

                    if is_armed and order_entry is not None:
                        # триггер входа
                        if (side=="LONG" and row.high>=order_entry) or (side=="SHORT" and row.low<=order_entry):
                            # последовательность: TP/SL
                            tp_hit=False; sl_hit=False
                            for j in range(i, min(i+48, len(dff))):
                                b = dff.iloc[j]
                                if side=="LONG":
                                    if b.low<=stop: sl_hit=True; break
                                    if b.high>=tp: tp_hit=True; break
                                else:
                                    if b.high>=stop: sl_hit=True; break
                                    if b.low<=tp: tp_hit=True; break
                            if tp_hit: wins+=1
                            elif sl_hit: losses+=1
                            break

            print(f"{symbol}: armed={armed} canceled={canceled} W={wins} L={losses}")
        except Exception as e:
            print(f"{symbol}: ошибка бэктеста: {e}")
    return True
