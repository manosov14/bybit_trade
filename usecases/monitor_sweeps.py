# bybit/usecases/monitor_sweeps.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Set, Any, Optional
import time
import os

import pandas as pd

# ==== инфраструктура/домены проекта ====
from infra.env import load_env, parse_add_levels
from infra.exchange_ccxt import ExchangeService
from infra.databroker import DataBroker

from domain.trend import d1_trend, h4_trend
from domain.levels import working_levels_d1, LevelsParams
from domain.sweep import check_sweep, SweepParams
from domain.speed_filter import fast_approach_ok
from domain.risk import RiskParams, position_sizing
from domain import sessions as sess
from domain.levels_engine import Level

# ==== обеспечение каталогов логов ====
try:
    os.makedirs("logs", exist_ok=True)
except Exception:
    # не критично, просто не пишем в файловый лог
    pass

# ==== безопасное чтение конфигурации ====
def safe_get(env: dict, key: str, default=None):
    try:
        return env.get(key, default)
    except Exception:
        return default

# ==== троттлер повторяющихся сообщений (не засоряем консоль) ====
_last_print: dict[str, float] = {}
def _throttle(key: str, sec: int = 10) -> bool:
    now = time.time()
    last = _last_print.get(key, 0.0)
    if now - last >= sec:
        _last_print[key] = now
        return True
    return False


# ==== компактная сводная таблица событий (PAIR шире, добавлены REASONS) ====
_summary_printed_header = False
def _print_summary_header():
    global _summary_printed_header
    if _summary_printed_header:
        return
    cols = ["TIME (UTC)", "PAIR", "SIDE", "LEVEL", "DEPTH(ATR TF)", "ENTRY", "SL", "TP", "QTY", "STATUS", "REASONS"]
    widths = [20, 16, 6, 14, 14, 14, 14, 14, 10, 10, 48]
    line = " | ".join(c.ljust(w) for c, w in zip(cols, widths))
    sep = "-+-".join("-"*w for w in widths)
    print(line); print(sep)
    _summary_printed_header = True

def _print_summary_row(sym, side, level, depth_atr, entry, sl, tp, qty, status, reasons=""):
    cols = ["TIME (UTC)", "PAIR", "SIDE", "LEVEL", "DEPTH(ATR TF)", "ENTRY", "SL", "TP", "QTY", "STATUS", "REASONS"]
    widths = [20, 16, 6, 14, 14, 14, 14, 14, 10, 10, 48]
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    def fmt(x, w):
        return ("" if x is None else str(x))[:w].ljust(w)
    row = [
        fmt(now, widths[0]),
        fmt(sym, widths[1]),
        fmt(side or "-", widths[2]),
        fmt(f"{float(level):.4f}" if level is not None else "-", widths[3]),
        fmt(f"{float(depth_atr):.3f}" if depth_atr is not None else "-", widths[4]),
        fmt(f"{float(entry):.4f}" if entry is not None else "", widths[5]),
        fmt(f"{float(sl):.4f}" if sl is not None else "", widths[6]),
        fmt(f"{float(tp):.4f}" if tp is not None else "", widths[7]),
        fmt(f"{float(qty):.6f}" if qty is not None else "", widths[8]),
        fmt(status, widths[9]),
        fmt(reasons or "", widths[10]),
    ]
    print(" | ".join(row))
# ---------- утилиты форматирования ----------
def _fmt_utc(x: Any) -> str:
    try:
        ts = pd.to_datetime(x, utc=True)
        if pd.isna(ts):
            return "-"
        return ts.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"

def _mk_table(rows: List[List[str]], headers: List[str]):
    if not rows:
        print("(no data)\n")
        return
    widths = [len(h) for h in headers]
    for r in rows:
        for j, cell in enumerate(r):
            widths[j] = max(widths[j], len(str(cell)))
    def line(sep="-+-"):
        return sep.join("-" * w for w in widths)
    def fmt_row(r):
        return " | ".join(str(r[j]).ljust(widths[j]) for j in range(len(widths)))
    print(fmt_row(headers))
    print(line())
    for r in rows:
        print(fmt_row(r))
    print()

def _pick_trade_side(d1v: str, h4v: str, d1_on: bool, h4_on: bool) -> str:
    if d1_on and h4_on:
        return d1v if d1v == h4v else f"CONFLICT {d1v}/{h4v}"
    if d1_on:
        return d1v
    if h4_on:
        return h4v
    return "DISABLED"

def _tf_minutes(tf: str) -> int:
    tf = tf.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1] or 1)
    if tf.endswith("h"):
        return int(tf[:-1] or 1) * 60
    return 1

def _bar_touches(bar: pd.Series, level: float, side: str, eps_pct: float) -> bool:
    px_high = float(bar["high"])
    px_low = float(bar["low"])
    eps = float(level) * (eps_pct / 100.0)
    if side == "LONG":
        return px_low <= level + eps
    else:
        return px_high >= level - eps

def _eff_eps_pct(level: float, pct: float, tick: Optional[float], ticks_mult: int) -> float:
    if tick and level > 0:
        tick_pct = (float(tick) * max(1, int(ticks_mult))) / float(level) * 100.0
        return max(float(pct), tick_pct)
    return float(pct)

def _speed_minutes(h1_df: pd.DataFrame,
                   level: float,
                   atr_h1: pd.Series,
                   half_atr_factor: float) -> Optional[float]:
    """
    Оценка, за сколько минут цена прошла половину ATR(H1) к уровню.
    Возвращает None, если оценка невозможна.
    """
    try:
        # текущая цена
        px = float(h1_df["close"].iloc[-1])
        half_atr = float(atr_h1.iloc[-1]) * float(half_atr_factor)
        # считаем от последней свечи назад, когда расстояние до уровня
        # стало <= половины ATR
        dist = abs(px - float(level))
        if dist <= half_atr:
            # оценка: сколько минут назад началось "ускорение"
            # грубо: 60 мин * число баров, если баров > 1
            return 0.0
        # попробуем найти индекс, когда пересекли половину ATR
        for i in range(len(h1_df) - 2, -1, -1):
            px_i = float(h1_df["close"].iloc[i])
            atr_i = float(atr_h1.iloc[i])
            if abs(px_i - float(level)) <= atr_i * float(half_atr_factor):
                bars = (len(h1_df) - 1) - i
                return float(bars * 60)
    except Exception:
        return None
    return None

# ---------- конфиг ----------
@dataclass
class MonitorCfg:
    days: int
    include_inside: bool
    sweep_tf: str
    sweep_atr_len: int
    sweep_min_atr_frac: float
    sweep_max_atr_frac: float
    closeback_bars: int
    speed_half_atr: float
    speed_max_minutes: int
    use_speed: bool
    sessions: List[str]
    d1_enabled: bool
    h4_enabled: bool
    account_equity: float
    risk_pct: float
    rr: float
    mode: str
    place_limit: bool
    interval_sec: int
    one_trade_per_level: bool
    scan_last_bars: int
    touch_eps_pct: float
    touch_eps_ticks: int
    preview_today_on_run: bool
    preview_today_max_events: int
    preview_signals_table: bool
    preview_ignore_trend: bool
    manual_levels: dict[str, float]
    max_live_trades: int
    priority_bases: List[str]
    entry_ticks: int
    stop_ticks: int
    place_stop: bool

def _get_cfg(env_path: str) -> MonitorCfg:
    env = load_env(env_path)
    b = lambda k, d: str(safe_get(env, k, d)).strip().lower() in ("1", "true", "on", "yes", "y")
    i = lambda k, d: int(safe_get(env, k, d))
    f = lambda k, d: float(safe_get(env, k, d))
    s = lambda k, d: str(safe_get(env, k, d))
    return MonitorCfg(
        days=i("DAYS", 10),
        include_inside=b("INCLUDE_INSIDE", True),
        sweep_tf=s("SWEEP_TF", "5m"),
        sweep_atr_len=i("SWEEP_ATR_LEN", 14),
        sweep_min_atr_frac=f("SWEEP_MIN_ATR_FRAC", 0.15),
        sweep_max_atr_frac=f("SWEEP_MAX_ATR_FRAC", 0.35),
        closeback_bars=i("CLOSEBACK_BARS", 5),
        speed_half_atr=f("SPEED_HALF_ATR", 0.3),
        speed_max_minutes=i("SPEED_MAX_MINUTES", 15),
        use_speed=b("USE_SPEED", True),
        sessions=[x.strip().upper() for x in s("SESSIONS", "EU,US,ASIA").split(",") if x.strip()],
        d1_enabled=b("D1", True),
        h4_enabled=b("H4", False),
        account_equity=f("ACCOUNT_EQUITY", 100),
        risk_pct=f("RISK_PCT", 1.0),
        rr=f("RR", 3.0),
        mode=s("MODE", "test").lower(),
        place_limit=b("LIMIT", False),
        interval_sec=i("MONITOR_INTERVAL_SEC", 5),
        one_trade_per_level=b("ONE_TRADE_PER_LEVEL", True),
        scan_last_bars=max(1, i("SCAN_LAST_BARS", 2)),
        touch_eps_pct=f("TOUCH_EPS_PCT", 0.01),
        touch_eps_ticks=i("TOUCH_EPS_TICKS", 1),
        preview_today_on_run=b("PREVIEW_TODAY_ON_RUN", True),
        preview_today_max_events=i("PREVIEW_TODAY_MAX_EVENTS", 50),
        preview_signals_table=b("PREVIEW_SIGNALS_TABLE", True),
        preview_ignore_trend=b("PREVIEW_IGNORE_TREND", False),
        manual_levels=parse_add_levels(env) or {},
        max_live_trades=i("MAX_LIVE_TRADES", 3),
        priority_bases=[(x.split("/")[0].split(":")[0]).strip().upper()
                        for x in s("SYMBOLS", "BTC/USDT,ETH/USDT").split(",") if x.strip()],
        entry_ticks=i("ENTRY_TICKS", 2),
        stop_ticks=i("STOP_TICKS", 3),
        place_stop=b("PLACE_STOP_MARKET", True),
    )

# ---------- PREVIEW / основной цикл мониторинга ----------
def _pre_scan_today(broker: DataBroker, cfg: MonitorCfg, syms: List[str], env_path: str):
    """
    Печатает «сигналы за сегодня» + онлайн-таблицу статусов по инструментам.
    Заказы не размещает, кроме случая MODE=live (тогда создаёт ExchangeService по .env).
    """
    print("\n=== PREVIEW: сегодня (UTC) — сигналы по пробоям уровней ===")

    # локальные структуры для уникализации
    traded_levels: Set[tuple[str, str, float]] = set()
    seen_bars: Set[tuple[str, float, str]] = set()
    live_placed = 0
    ex: Optional[ExchangeService] = None
    if cfg.mode == "live":
        try:
            ex = ExchangeService(env_path)  # не обязателен для тестового режима
        except Exception as e:
            if _throttle(f"ex-init:{e}"):
                print(f"[monitor warn] exchange init failed: {e}")

    # d1/h4 SMA из env (имена не меняем)
    env = load_env(env_path)
    d1_sma = int(safe_get(env, "D1_SMA", 200))
    h4_sma = int(safe_get(env, "H4_SMA", 50))

    headers = ["Symbol","TimeUTC","Level","Trends","Speed","DepthATR","Decision","PnL(R)","EntryType","Status"]
    # rows печатаются по мере событий фильтров (ниже)

    for sym in syms:
        try:
            d1 = broker.get_ohlcv(sym, "1d", need=250)
            h4 = broker.get_ohlcv(sym, "4h", need=250) if cfg.h4_enabled else None

            # === FIX: нормализованные отступы ===
            d1v = d1_trend(d1, d1_sma)
            h4v = h4_trend(h4, h4_sma) if h4 is not None else d1v

            streams: List[tuple[str, List[Level]]] = []

            # Stream A: D1 (PD + SW + MANUAL)
            if cfg.d1_enabled:
                lvls_a = working_levels_d1(
                    d1, d1v,
                    LevelsParams(days_window=cfg.days, include_inside_days=cfg.include_inside)
                )
                # добавим MANUAL-уровень к D1-потоку, если задан в .env и нет дубля
                try:
                    _base = (sym.split("/")[0].split(":")[0]).strip().upper()
                    _ml = cfg.manual_levels.get(_base)
                    if _ml is not None and all(float(x.price) != float(_ml) for x in lvls_a):
                        lvls_a.append(Level(ts=d1.iloc[-1]["ts"], kind="H4", price=float(_ml)))
                except Exception:
                    pass
                if lvls_a:
                    streams.append((d1v, lvls_a))

            # Stream B: H4 (MANUAL only)
            if cfg.h4_enabled:
                try:
                    _base = (sym.split("/")[0].split(":")[0]).strip().upper()
                    _ml = cfg.manual_levels.get(_base)
                except Exception:
                    _ml = None
                if _ml is not None:
                    streams.append((h4v, [Level(ts=d1.iloc[-1]["ts"], kind="H4", price=float(_ml))]))

            if not streams:
                continue

            # поддержка one-trade-per-level: удалим уровни, ушедшие из актуального списка (чистка)
            current_prices = {(float(l.price), side) for side, lv in streams for l in lv}
            traded_levels = {
                k for k in traded_levels
                if not (k[0] == sym and (k[2], k[1]) not in current_prices)
            }

            intr = broker.get_ohlcv(sym, cfg.sweep_tf,
                                    need=max(cfg.scan_last_bars, cfg.sweep_atr_len + 5)).copy()
            last_slice = intr.tail(max(cfg.scan_last_bars, 2))
            tick = broker.get_tick(sym)

            for trade, lvls in streams:
                for lvl in lvls:
                    eps_pct_eff = _eff_eps_pct(float(lvl.price), cfg.touch_eps_pct, tick, cfg.touch_eps_ticks)
                    touching = [
                        bar for _, bar in last_slice.iterrows()
                        if _bar_touches(bar, lvl.price, trade, eps_pct_eff)
                    ]
                    if not touching:
                        continue

                    bar = touching[-1]
                    bar_ts = str(bar.get("ts"))
                    bar_key = (sym, float(lvl.price), bar_ts)
                    if bar_key in seen_bars:
                        continue

                    # ATR(tf)
                    tr_tf = pd.DataFrame({
                        "hl": intr["high"] - intr["low"],
                        "hc": (intr["high"] - intr["close"].shift(1)).abs(),
                        "lc": (intr["low"] - intr["close"].shift(1)).abs(),
                    })
                    atr_tf = tr_tf.max(axis=1).rolling(cfg.sweep_atr_len, min_periods=1).mean().ffill()
                    atr_tf_last = float(atr_tf.iloc[-1]) if atr_tf.shape[0] else 1e-9
                    if atr_tf_last <= 0:
                        atr_tf_last = 1e-9

                    # фильтры (покажем таблицей как и раньше)
                    rows_f = []

                    otp_key = (sym, trade, float(lvl.price))
                    one_trade_ok = not (cfg.one_trade_per_level and otp_key in traded_levels)
                    rows_f.append(("one-trade-per-level", "first" if one_trade_ok else "already",
                                  "unique until level disappears", "ok" if one_trade_ok else "fail"))

                    if cfg.use_speed:
                        h1 = broker.get_ohlcv(sym, "1h", need=60, warm=True)
                        tr_h1 = pd.DataFrame({
                            "hl": h1["high"] - h1["low"],
                            "hc": (h1["high"] - h1["close"].shift(1)).abs(),
                            "lc": (h1["low"] - h1["close"].shift(1)).abs(),
                        })
                        atr_h1 = tr_h1.max(axis=1).rolling(14, min_periods=1).mean().ffill()

                        speed_ok = fast_approach_ok(
                            h1, float(lvl.price), atr_h1, cfg.speed_half_atr, cfg.speed_max_minutes
                        )
                        try:
                            minutes_val = _speed_minutes(h1, float(lvl.price), atr_h1, cfg.speed_half_atr)
                            minutes_cell = str(int(round(minutes_val))) if minutes_val is not None else "-"
                        except Exception:
                            minutes_cell = "-"
                        rows_f.append(("speed", minutes_cell,
                                      f"<= {cfg.speed_max_minutes} min from {cfg.speed_half_atr}*ATR(H1)",
                                      "ok" if speed_ok else "fail"))
                    else:
                        speed_ok = True
                        rows_f.append(("speed", "skipped", "USE_SPEED=false", "ok"))

                    eps = float(lvl.price) * (eps_pct_eff / 100.0)
                    if trade == "SHORT":
                        pierce_raw = max(0.0, float(bar["high"]) - (float(lvl.price) - eps))
                    else:
                        pierce_raw = max(0.0, (float(lvl.price) + eps) - float(bar["low"]))
                    pierce_now_pct = pierce_raw / atr_tf_last
                    depth_ok = (cfg.sweep_min_atr_frac <= pierce_now_pct <= cfg.sweep_max_atr_frac)
                    rows_f.append(("sweep.depth", f"{pierce_now_pct:.3f} ATR",
                                   f"{cfg.sweep_min_atr_frac}..{cfg.sweep_max_atr_frac}",
                                   "ok" if depth_ok else "fail"))

                    window = intr.iloc[-(cfg.closeback_bars + 6):][["ts","open","high","low","close"]].reset_index(drop=True)
                    sw = check_sweep(
                        window, lvl.price, trade,
                        SweepParams(
                            atr_len=cfg.sweep_atr_len,
                            min_atr_frac=cfg.sweep_min_atr_frac,
                            max_atr_frac=cfg.sweep_max_atr_frac,
                            max_closeback_bars=cfg.closeback_bars,
                            timeframe_minutes=_tf_minutes(cfg.sweep_tf),
                        )
                    )
                    closeback_ok = sw.is_sweep
                    rows_f.append((
                        "sweep.closeback", "returned" if closeback_ok else "no-return",
                        f"<= {cfg.closeback_bars} bars", "ok" if closeback_ok else "fail"
                    ))

                    now = datetime.now(timezone.utc)
                    sess_ok = sess.is_session_allowed(now, cfg.sessions, custom=None)
                    rows_f.append(("session", f"UTC {now.hour:02d}:xx", ",".join(cfg.sessions),
                                   "ok" if sess_ok else "fail"))

                    # печать фильтров
                    _mk_table([list(r) for r in rows_f], ["FILTER","VALUE","RANGE/THRESH","PASS"])

                    all_ok = one_trade_ok and speed_ok and depth_ok and sess_ok
                    tick = broker.get_tick(sym) or 0.0
                    entry = float(lvl.price + (cfg.entry_ticks * tick if trade == "LONG" else -cfg.entry_ticks * tick))
                    stop = float(lvl.price - (cfg.stop_ticks * tick)) if trade == "LONG" else float(lvl.price + (cfg.stop_ticks * tick))
                    rk = RiskParams(account_equity=cfg.account_equity, risk_pct=cfg.risk_pct, rr=cfg.rr, leverage=3.0)
                    ps = position_sizing(entry, stop, trade, rk)

                    if not all_ok:
                        failed = [r[0] for r in rows_f if str(r[3]).lower() != "ok"]
                        print("➡ RESULT: REJECT — " + (f"failed: {', '.join(failed)}. " if failed else "") +
                              f"entry={entry}, sl={ps.sl}, tp={ps.tp}, qty≈{ps.qty:.6f}\n")
                    else:
                        side = "buy" if trade == "LONG" else "sell"
                        msg = (f"➡ RESULT: RECOMMEND {side.upper()} @ {entry} | SL {ps.sl} | TP {ps.tp} | "
                               f"QTY≈{ps.qty:.6f} (MODE={cfg.mode})")
                        if cfg.mode == "live" and ex is not None:
                            try:
                                if live_placed >= cfg.max_live_trades:
                                    msg += " | SKIP: max_live_trades reached"
                                else:
                                    order = ex.ex.create_order(
                                        sym, "market", side, ps.qty, None,
                                        params={
                                            "stopPrice": entry,
                                            "triggerDirection": (1 if side == "buy" else 2),
                                            "reduceOnly": False,
                                            "timeInForce": "GTC",
                                            "takeProfit": ps.tp,
                                            "stopLoss": ps.sl,
                                        }
                                    )
                                    live_placed += 1
                                    msg += f" | ORDER: placed id={order.get('id','?')}"
                            except Exception as e:
                                if _throttle(f"order-error:{e}"):
                                    msg += f" | ORDER ERROR: {e}"
                        print(msg + "\n")
                        if cfg.one_trade_per_level:
                            traded_levels.add((sym, trade, float(lvl.price)))

                    seen_bars.add(bar_key)

            time.sleep(cfg.interval_sec)

        except KeyboardInterrupt:
            print("Stopped by user.")
            break
        except Exception as e:
            # троттлим повторяющиеся ошибки окружения (ключи/API)
            if _throttle(f"monitor-error:{e}"):
                print(f"[monitor error] {e}")
            time.sleep(3)
