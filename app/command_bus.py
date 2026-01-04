from __future__ import annotations
import os
# app/command_bus.py

# --------------------------- описания переменных окружения ---------------------------
ENV_DESCRIPTIONS = {
    "MODE": "Режим работы: test — демо; live — реальная торговля.",
    "TESTNET": "Включить тестовую среду Bybit (true/false).",
    "SYMBOLS": "Список торгуемых инструментов BASE/QUOTE:CONTRACT через запятую.",
    "ADD_LEVELS": "Ручные уровни на сегодня (пример: BTC:68000; ETH:3600).",
    "D1_SMA_LEN": "Длина SMA для тренда D1.",
    "H4_SMA_LEN": "Длина SMA для тренда H4.",
    "ENABLE_D1": "Включить фильтр тренда D1 (true/false).",
    "ENABLE_H4": "Включить фильтр тренда H4 (true/false).",
    "DAYS": "Сколько дней брать для уровней D1.",
    "INCLUDE_INSIDE": "Учитывать inside-days при поиске уровней.",
    "ATR_M5_LEN": "ATR(M5) для близости к уровню.",
    "ATR_H1_LEN": "ATR(H1) для скорости/глубины.",
    "MIN_SWEEP_ATR_H1": "Мин. глубина прокола (в ATR H1).",
    "MAX_SWEEP_ATR_H1": "Макс. глубина прокола (в ATR H1).",
    "RETURN_BARS_5M": "Окно возврата за уровень (баров M5).",
    "USE_SPEED": "Фильтр скорости подхода (true/false).",
    "SPEED_LOOKBACK_H1": "Окно оценки скорости (баров H1).",
    "SPEED_ACCEPT_FROM": "Мин.скорость (доли ATR H1).",
    "SPEED_ACCEPT_TO": "Макс.скорость (доли ATR H1).",
    "ENTRY_TICKS": "Смещение входа за уровень (тики).",
    "STOP_TICKS": "Смещение стопа за экстремум (тики).",
    "STOP_BY_SWEEP": "Стоп за экстремум прокола (true/false).",
    "RR": "Целевой RR.",
    "ACCOUNT_EQUITY": "Капитал (для расчёта размера позиции).",
    "RISK_PCT": "Риск на сделку, %.",
    "MAX_OPEN_TRADES": "Максимум одновременных сделок.",
    "STOP_SERIES_LIMIT": "Лимит серии стопов.",
    "SLIPPAGE_CANCEL_MINUTES": "Таймаут снятия неисполненной заявки, мин.",
    "MAX_ATTEMPTS_PER_SIGNAL": "Попыток по одному сигналу.",
    "PLACE_STOP_MARKET": "Ставить stop-market вместо limit (true/false).",
    "REENTRY_H1_PLUS_MIN": "Окно повторного входа после SL (минут сверх H1).",
    "USE_SESSIONS": "Фильтр сессий (true/false).",
    "SESSIONS": "Разрешённые сессии: EU,US,ASIA.",
    "SCAN_INTERVAL_SEC": "Пауза между итерациями сканера, сек.",
    "SHOW_REJECTED": "Показывать отфильтрованные события (true/false).",
    "PREMARKET_SCAN": "Ретроскан сегодняшнего дня перед онлайном.",
    "PREMARKET_DRIVER": "Таймфрейм ретроскана: 1m/5m.",
    "WARMUP_SWEEP_BARS": "Разогрев: сколько M1 баров загрузить.",
    "WARMUP_H1_BARS": "Разогрев: сколько H1 баров загрузить.",
    "PREVIEW_TODAY_ON_RUN": "Печатать «События за сегодня» при старте.",
    "PREVIEW_TODAY_MAX_EVENTS": "Лимит строк предпросмотра событий.",
    "PREVIEW_SIGNALS_TABLE": "Печатать таблицу статуса при старте.",
    "PREVIEW_IGNORE_TREND": "Игнорировать тренд в предпросмотре.",
}

from usecases.run_tables import print_today_events_table, print_symbols_snapshot

from dataclasses import dataclass
from typing import List, Optional

try:
    from infra.env import load_env, parse_add_levels, normalize_env
except Exception:
    from infra.env import load_env
    def parse_add_levels(env):
        return {}
from infra.exchange_ccxt import ExchangeService
from infra.databroker import DataBroker  # единый источник свечей/кэш

from domain.trend import d1_trend, h4_trend
from domain.levels import working_levels_d1, LevelsParams
from usecases.runtime import monitor_loop  # /run (new runtime)

# --------------------------- утилиты вывода ---------------------------

def _w(colwidths: List[int], row: List[str]) -> str:
    def _cell(i: int, val: str) -> str:
        s = "" if val is None else str(val)
        # keep empty cells as spaces for readability
        return s.ljust(colwidths[i])
    # pad row if shorter
    padded = list(row) + [""] * (len(colwidths) - len(row))
    return " | ".join(_cell(i, v) for i, v in enumerate(padded))


def _print_kv_section(title: str, pairs):
    print(f"{title}")
    col_w = 22
    for k, v in pairs:
        ks = str(k).ljust(col_w)
        print(f"{ks} | {v}")
    print()


def _print_table(headers: List[str], rows: List[List[str]]) -> None:
    # compute column widths allowing rows with more cells than headers
    widths = [len(h) for h in headers]
    for r in rows:
        # extend widths if row has more cells than headers
        if len(r) > len(widths):
            widths.extend([0] * (len(r) - len(widths)))
        for i, cell in enumerate(r):
            if i >= len(widths):
                widths.append(0)
            widths[i] = max(widths[i], len(str(cell)))
    sep_line = "-+-".join("-" * w for w in widths)
    # pad headers to full width
    hdr = headers + [""] * (len(widths) - len(headers))
    print(_w(widths, hdr))
    print(sep_line)
    for r in rows:
        # render horizontal separator if row is ['<hr>'] or ['—']
        if len(r) == 1 and str(r[0]).strip() in ("<hr>", "—", "<line>"):
            print(sep_line)
            continue
        print(_w(widths, r))
    print()
def _print_table_wrapped(headers: List[str], rows: List[List[str]], widths: List[int]) -> None:
    from textwrap import wrap
    hdr = headers + [""] * (len(widths) - len(headers))
    sep_line = "-+-".join("-"*w for w in widths)
    print(" | ".join(h.ljust(w) for h,w in zip(hdr, widths)))
    print(sep_line)
    for r in rows:
        if len(r)==1 and str(r[0]).strip() in ("<hr>","—","<line>"):
            print(sep_line); continue
        cells = list(r) + [""]*(len(widths)-len(r))
        cols = []
        maxh = 1
        for i,c in enumerate(cells):
            s = "" if c is None else str(c)
            lines = wrap(s, width=widths[i]) if len(s)>widths[i] else [s]
            cols.append(lines); maxh = max(maxh, len(lines))
        for i in range(maxh):
            row = [ (cols[j][i] if i<len(cols[j]) else "").ljust(widths[j]) for j in range(len(widths)) ]
            print(" | ".join(row))
    print()



def _pick_trade_side(d1v: str, h4v: str, use_d1: bool, use_h4: bool) -> str:
    if use_d1 and use_h4:
        return d1v if d1v == h4v else f"CONFLICT {d1v}/{h4v}"
    if use_d1:
        return d1v
    if use_h4:
        return h4v
    return "DISABLED"

def _symbols_from_env(env: dict) -> List[str]:
    raw = env.get("SYMBOLS", "BTC/USDT,ETH/USDT")
    return [s.strip() for s in raw.split(",") if s.strip()]

# --------------------------- состояние CLI ---------------------------

@dataclass
class RuntimeOverrides:
    days_window: Optional[int] = None
    include_inside: Optional[bool] = None

# --------------------------- /param: форматирование ---------------------------

def _boolify(val: str) -> Optional[bool]:
    if val is None:
        return None
    v = str(val).strip().lower()
    if v in ("1", "true", "yes", "on", "y"):
        return True
    if v in ("0", "false", "no", "off", "n"):
        return False
    return None

def _fmt_val(key: str, val: Optional[str], bool_keys: set[str]) -> str:
    if val is None or str(val).strip() == "":
        return "—"
    if key in bool_keys:
        b = _boolify(val)
        return "да" if b is True else ("нет" if b is False else str(val))
    return str(val)

def _chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

# --------------------------- команды ---------------------------

def cmd_help() -> None:
    print("Interactive mode. Commands: /param, /trend, /days [N], /levels, /run, /help, /quit\n")



def cmd_param(env_path: str) -> None:
    env = normalize_env(load_env(env_path))
    # полный список ключей для таблицы
    keys = [
        "MODE","TESTNET","SYMBOLS","ADD_LEVELS",
        "D1_SMA_LEN","H4_SMA_LEN","ENABLE_D1","ENABLE_H4","DAYS","INCLUDE_INSIDE",
        "ATR_M5_LEN","ATR_H1_LEN","MIN_SWEEP_ATR_H1","MAX_SWEEP_ATR_H1","RETURN_BARS_5M",
        "USE_SPEED","SPEED_LOOKBACK_H1","SPEED_ACCEPT_FROM","SPEED_ACCEPT_TO",
        "ENTRY_TICKS","STOP_TICKS","STOP_BY_SWEEP","RR","ACCOUNT_EQUITY","RISK_PCT","MAX_OPEN_TRADES","STOP_SERIES_LIMIT","SLIPPAGE_CANCEL_MINUTES","MAX_ATTEMPTS_PER_SIGNAL","PLACE_STOP_MARKET","REENTRY_H1_PLUS_MIN",
        "USE_SESSIONS","SESSIONS",
        "SCAN_INTERVAL_SEC","SHOW_REJECTED","PREMARKET_SCAN","PREMARKET_DRIVER",
        "WARMUP_SWEEP_BARS","WARMUP_H1_BARS","PREVIEW_TODAY_ON_RUN","PREVIEW_TODAY_MAX_EVENTS","PREVIEW_SIGNALS_TABLE","PREVIEW_IGNORE_TREND",
    ]
    bool_keys = {k for k in keys if k.startswith("USE_") or k.startswith("ENABLE_") or k in {"TESTNET","STOP_BY_SWEEP","PLACE_STOP_MARKET","PREVIEW_TODAY_ON_RUN","PREVIEW_SIGNALS_TABLE","PREVIEW_IGNORE_TREND","PREMARKET_SCAN","SHOW_REJECTED"}}
    def pr(k: str) -> str:
        v = env.get(k, "")
        if k in bool_keys:
            sv = str(v).strip().lower()
            if sv in ("1","true","yes","y","on"): return "да"
            if sv in ("0","false","no","n","off"): return "нет"
            return str(v)
        return str(v)
    rows = []
    for k in keys:
        rows.append([k, pr(k), ENV_DESCRIPTIONS.get(k, "")])
    _print_table_wrapped(["Параметр","Значение","Описание"], rows, [24, 18, 80])

def cmd_trend(env_path: str) -> None:
    env = load_env(env_path)
    manual_levels = parse_add_levels(env)
    symbols = _symbols_from_env(env)
    use_d1 = str(env.get("D1", "true")).lower() in ("1", "true", "yes", "on", "y")
    use_h4 = str(env.get("H4", "false")).lower() in ("1", "true", "yes", "on", "y")
    d1_sma = int(env.get("D1_SMA_LEN", 200))
    h4_sma = int(env.get("H4_SMA_LEN", 50))

    ex = ExchangeService()
    broker = DataBroker(ex, env_path)

    rows: List[List[str]] = []
    for s in symbols:
        try:
            # полный D1
            d1_df = broker.get_ohlcv(s, "1d", need=250)
            h4_df = broker.get_ohlcv(s, "4h", need=250)

            d1v = d1_trend(d1_df, d1_sma)
            h4v = h4_trend(h4_df, h4_sma) if h4_df is not None else '-'
            rows.append([s, d1v, h4v])
        except Exception as e:
            rows.append([s, f"ERR:{e}", "-", "-"])

    _print_table(["SYMBOL", "D1", "H4"], rows)

def cmd_levels(env_path: str, overrides: RuntimeOverrides) -> None:
    import pandas as pd

    env = load_env(env_path)
    manual_levels = parse_add_levels(env)
    symbols = _symbols_from_env(env)
    use_d1 = str(env.get("D1", "true")).lower() in ("1", "true", "yes", "on", "y")
    use_h4 = str(env.get("H4", "false")).lower() in ("1", "true", "yes", "on", "y")
    d1_sma = int(env.get("D1_SMA_LEN", 200))
    h4_sma = int(env.get("H4_SMA_LEN", 50))

    days_window = overrides.days_window if overrides.days_window is not None else int(env.get("DAYS", 10))
    include_inside = overrides.include_inside if overrides.include_inside is not None else (
        str(env.get("INCLUDE_INSIDE", "true")).lower() in ("1", "true", "yes", "on", "y")
    )
    use_sw_levels = False

    ex = ExchangeService()
    broker = DataBroker(ex, env_path)

    headers = ["SYMBOL", "SIDE", "INSIDE_DAYS", "PD", "MANUAL"] if not use_sw_levels else ["SYMBOL", "SIDE", "INSIDE_DAYS", "PD", "SW", "MANUAL"]
    rows: List[List[str]] = []

    for s in symbols:
        try:
            # полный D1
            d1_df = broker.get_ohlcv(s, "1d", need=250)
            h4_df = broker.get_ohlcv(s, "4h", need=250)

            d1v = d1_trend(d1_df, d1_sma)
            h4v = h4_trend(h4_df, h4_sma) if h4_df is not None else '-'
            side = d1v if use_d1 else (h4v if use_h4 else d1v)
            # Кол-во внутренних дней за последние DAYS (без сегодняшнего)
            inside_series = (d1_df["high"] <= d1_df["high"].shift(1)) & (d1_df["low"] >= d1_df["low"].shift(1))
            if len(inside_series) > 0:
                inside_excl_today = inside_series.iloc[:-1]  # исключаем текущий день
            else:
                inside_excl_today = inside_series
            inside_count = int(inside_excl_today.tail(days_window).sum())

            lvls = working_levels_d1(
                d1_df,
                side,
                LevelsParams(days_window=days_window, include_inside_days=include_inside),
            )
            if not lvls:
                rows.append([s, side, str(inside_count), "(no levels)"])
                continue

            # Собираем уровни — PD | SW | MANUAL
            def _trim(x: float) -> str:
                try:
                    s = f"{float(x):.8f}".rstrip('0').rstrip('.')
                    return s if s else "0"
                except Exception:
                    return str(x)

            def _kind_alias(tag: str) -> str:
                t = str(tag).upper()
                if t in ("SWINGL","SWINGH"): return "sw"
                return t

            cols = {"PD": [], "MANUAL": []} if not use_sw_levels else {"PD": [], "SW": [], "MANUAL": []}
            for l in lvls:
                k = _kind_alias(l.kind)
                if k in ("PDL", "PDH"):
                    cols["PD"].append(_trim(l.price))
                else:
                    pass
                        # Сортировка: от ближайших к текущей цене к более дальним
            try:
                ref_price = float(d1_df['close'].iloc[-1])
            except Exception:
                ref_price = None
            def _sort_by_nearness(str_vals):
                try:
                    nums = [float(x) for x in str_vals]
                    if ref_price is None:
                        nums_sorted = sorted(nums)
                    else:
                        nums_sorted = sorted(nums, key=lambda v: abs(v - ref_price))
                    return [ _trim(v) for v in nums_sorted ]
                except Exception:
                    return sorted(str_vals)
            cols['PD'] = _sort_by_nearness(cols['PD'])
            cols['SW'] = _sort_by_nearness(cols['SW']) if use_sw_levels else []

            # Ручной уровень (на сегодня)
            base = (s.split('/')[0].split(':')[0]).strip().upper()
            level_prices = [float(l.price) for l in lvls]
            ml = manual_levels.get(base)
            if ml is not None:
                ml_list = ml if isinstance(ml, (list, tuple)) else [ml]
                for _v in ml_list:
                    try:
                        fv = float(_v)
                    except Exception:
                        continue
                    cols["MANUAL"].append(_trim(fv))

            # Разбиваем каждую колонку на группы по 4 значения для читабельности
            def _chunks(lst, n):
                return [lst[i:i+n] for i in range(0, len(lst), n)] or [[]]

            ch_pd  = _chunks(cols["PD"], 4)
            ch_sw  = [[]]
            ch_man = _chunks(cols["MANUAL"], 4)

            max_rows = max(len(ch_pd), len(ch_man))
            while len(ch_pd)  < max_rows: ch_pd.append([])
            while len(ch_sw)  < max_rows: ch_sw.append([])
            while len(ch_man) < max_rows: ch_man.append([])

            for j in range(max_rows):
                pd_str  = ", ".join(ch_pd[j])  if ch_pd[j]  else ""
                sw_str  = ", ".join(ch_sw[j])  if ch_sw[j]  else ""
                man_str = ", ".join(ch_man[j]) if ch_man[j] else ""
                if use_sw_levels:
                    if j == 0:
                        rows.append([s, side, str(inside_count), pd_str, sw_str, man_str])
                    else:
                        rows.append(["", "", "", pd_str, sw_str, man_str])
                else:
                    if j == 0:
                        rows.append([s, side, str(inside_count), pd_str, man_str])
                    else:
                        rows.append(["", "", "", pd_str, man_str])
            rows.append(["<hr>"])
        except Exception as e:
            rows.append([s, "ERR", "-", f"{e}"])

    _print_table(headers, rows)

def cmd_days(env_path: str, overrides: RuntimeOverrides, arg: Optional[str]) -> None:
    """/days N — печатает результаты бэктеста в формате таблицы фильтров.
    Если модуль usecases.backtest_days доступен — используем его результаты.
    Иначе — сохраняем обратную совместимость (только установка окна DAYS).
    """
    try:
        n = int(arg) if arg is not None else None
    except Exception:
        print("Usage: /days N (N = integer)\n"); return
    if n is None:
        print("Usage: /days N\n"); return
    overrides.days_window = n
    env = load_env(env_path)

    # Попробуем импортировать usecase бэктеста
    try:
        import importlib
        bt = importlib.import_module("usecases.backtest_days")
    except Exception:
        print(f"Days window set to {n}. Модуль бэктеста не найден — активировано только окно DAYS для /levels и /run.\n")
        return

    # Попытаемся найти подходящую функцию
    candidate_funcs = [
        "backtest_days", "run_backtest_days", "run", "collect_breakouts", "compute"
    ]
    fn = None
    for name in candidate_funcs:
        f = getattr(bt, name, None)
        if callable(f):
            fn = f; break
    if fn is None:
        print(f"Days window set to {n}. Бэктест-исполнитель не обнаружен в usecases.backtest_days.\n")
        return

    # Выполним бэктест. Ожидаем либо dict[symbol]->list[event], либо list[event] с ключом 'symbol'.
    try:
        result = None
        try:
            result = fn(env_path=env_path, days=n)  # наиболее вероятная сигнатура
        except TypeError:
            try:
                result = fn(env, n)  # запасная
            except Exception:
                result = fn(n)  # крайний вариант
    except Exception as e:
        print(f"Бэктест завершился с ошибкой: {e}\n")
        return

    # Нормализуем структуру результата к dict[str, list[dict]]
    def _normalize(res):
        if isinstance(res, dict):
            return res
        if isinstance(res, list):
            out = {}
            for ev in res:
                sym = (ev.get("symbol") if isinstance(ev, dict) else None) or "UNKNOWN"
                out.setdefault(sym, []).append(ev)
            return out
        return {}

    res = _normalize(result)
    if not res:
        print(f"{n} дней: нет событий для печати.\n")
        return

    # Печать по каждому символу
    for sym, events in res.items():
        print(f"{sym} — анализ {n} дней")
        if not events:
            print("(нет событий)\n"); continue

        # Сортировка по времени, если доступно
        try:
            events = sorted(events, key=lambda e: e.get("ts") or e.get("ts_touch") or 0)
        except Exception:
            pass

        for ev in events:
            side = ev.get("side", "?")
            entry = ev.get("entry") or ev.get("entry_price") or ev.get("price") or 0.0
            ts = ev.get("ts_touch") or ev.get("ts") or ev.get("time") or ""

            # Вычислим поля для таблицы
            # Тренд: числа (1/-1), если доступны строки — преобразуем
            def _trend_to_num(v):
                s = str(v or "").lower()
                if s in ("up","long","bull","bullish","вверх","лонг","long_only","buy"): return 1
                if s in ("down","short","bear","bearish","вниз","шорт","sell"): return -1
                try:
                    return int(v)
                except Exception:
                    return 0

            d1v = _trend_to_num(ev.get("trend_d1"))
            h4v = _trend_to_num(ev.get("trend_h4", d1v))

            speed_min = ev.get("speed_minutes") or ev.get("speed_min") or ev.get("speed") or 0
            speed_max = ev.get("speed_max_minutes") or ev.get("speed_limit") or 0

            depth_atr = ev.get("depth_atr") or ev.get("sweep_depth_atr") or 0.0
            depth_min = ev.get("depth_min_atr_frac") or 0.0
            depth_max = ev.get("depth_max_atr_frac") or 0.0

            close_bars = ev.get("closeback_bars") or ev.get("bars_to_closeback") or 0
            close_max = ev.get("closeback_max_bars") or 0

            sess_hour = ev.get("session_hour") or ev.get("hour") or 0
            sess_allowed = ev.get("session_allowed") or ev.get("session_ok") or False

            one_per_level_ok = ev.get("one_trade_per_level_ok", True)

            # Флаги прохождения
            ok_speed = ev.get("ok_speed", (speed_max == 0 or (speed_min and speed_max and speed_min <= speed_max)))
            ok_depth = ev.get("ok_depth", (depth_min <= depth_atr <= (depth_max or depth_min)))
            ok_close = ev.get("ok_close", (close_max == 0 or close_bars <= close_max))
            ok_sess = ev.get("ok_session", bool(sess_allowed))

            # Печать заголовка события
            print(f"[{sym}] {side.upper()} @ {float(entry):.2f} | ts={ts}")

            headers = ["ФИЛЬТР", "ЗНАЧЕНИЕ", "ДИАПАЗОН/ПОРОГ", "ПРОПУСКАТЬ"]
            rows = [
                ["Тренд (D1 \\ H4)", f"{d1v} \\ {h4v}", "Flag D1 \\ Flag H4", "ok"],
                ["скорость", f"{int(speed_min)} м", f"<= {int(speed_max)} м", "ok" if ok_speed else "skip"],
                ["развертка.глубина", f"{float(depth_atr):.3f} ATR", f"{float(depth_min):.2f}..{float(depth_max):.2f}", "ok" if ok_depth else "skip"],
                ["развертка.закрытие", f"{int(close_bars)}", f"<= {int(close_max)} баров", "ok" if ok_close else "skip"],
                ["сессия", f"{int(sess_hour)}", "ЕС, США, АЗИЯ", "ok" if ok_sess else "skip"],
            ]
            _print_table(headers, rows)

            summary = (
                f"[{sym}] {side.upper()} @ {float(entry):.2f} | "
                f"скорость = {'OK' if ok_speed else 'FAIL'} ({int(speed_min)} м) | "
                f"глубина = {'OK' if ok_depth else 'FAIL'} ({float(depth_atr):.3f} ATR) | "
                f"закрытие = {'OK' if ok_close else 'FAIL'} | "
                f"сессия = {'OK' if ok_sess else 'FAIL'} | "
                f"1 сделка/уровень={'ОК' if one_per_level_ok else 'НЕТ'}."
            )
            print(summary + "\n")
        print("Usage: /days N")
        return
    try:
        n = int(arg)
        overrides.days_window = n
        print(f"Days window set to {n} (runtime override). Теперь /levels и /run будут использовать DAYS={n}.\n")
    except Exception:
        print("Usage: /days N (N = integer)\n")

def cmd_run(env_path: str) -> None:
    try:
        monitor_loop(env_path)
    except KeyboardInterrupt:
        print("\n[run] Остановлено пользователем.")

# --------------------------- цикл обработки ---------------------------

def handle_command(cmdline: str, env_path: str = ".env", days: Optional[int] = None) -> bool:
    static_overrides = handle_command._overrides  # type: ignore[attr-defined]

    parts = cmdline.strip().split()
    if not parts:
        return True

    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None

    if cmd in ("/help", "help", "?"):
        cmd_help(); return True
    if cmd in ("/param", "/params", "/env"):
        cmd_param(env_path); return True
    if cmd == "/trend":
        cmd_trend(env_path); return True
    if cmd == "/levels":
        cmd_levels(env_path, static_overrides); return True
    if cmd == "/days":
        # prefer provided 'days' kwarg; fallback to arg
        arg_val = (str(days) if days is not None else arg)
        cmd_days(env_path, static_overrides, arg_val); return True
    if cmd == "/run":
        before_run_tables(env_path)
        cmd_run(env_path); return True
    if cmd in ("/quit", "/exit"):
        print("Bye"); return False

    print("Unknown command. Try /help\n"); return True

handle_command._overrides = RuntimeOverrides()  # type: ignore[attr-defined]

def main():
    env_path = ".env"
    print("Interactive mode. Commands: /param, /trend, /days [N], /levels, /run, /help, /quit")
    print(f"Using .env: {env_path}\n")
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye"); break
        if not handle_command(line, env_path):
            break

if __name__ == "__main__":
    main()

def before_run_tables(env_path: str):
    return

