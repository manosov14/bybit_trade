from __future__ import annotations
from typing import List, Dict, Any
from datetime import datetime, timezone
import time, os, json

from infra.env import load_env
from scanner import ModularScanner
from domain.trend import d1_trend, h4_trend

def _norm_symbol(s: str) -> str:
    return s[:-2] + ":USDT" if s.endswith(":U") else s

def _header(cols, widths):
    return " | ".join(c.ljust(w) for c,w in zip(cols, widths)) + "\n" + "-+-".join("-"*w for w in widths)

def _row(values, widths):
    vals = [(str(v) if v is not None else '-') for v in values]
    return ' | '.join(v[:w].ljust(w) for v,w in zip(vals, widths))

def _fmt_msk(ts_iso)->str:
    try:
        from datetime import datetime, timedelta, timezone
        if ts_iso is None:
            return '-'
        # int/float epoch seconds
        if isinstance(ts_iso, (int, float)):
            dt = datetime.utcfromtimestamp(float(ts_iso)).replace(tzinfo=timezone.utc)
        else:
            s = str(ts_iso)
            if s.isdigit():
                dt = datetime.utcfromtimestamp(float(s)).replace(tzinfo=timezone.utc)
            else:
                dt = datetime.fromisoformat(s.replace('Z','+00:00'))
        MSK = timezone(timedelta(hours=3))
        return dt.astimezone(MSK).strftime('%H:%M:%S')
    except Exception:
        return str(ts_iso)[:8] if ts_iso is not None else '-'


def _fmt_utc(ts_iso):
    """Normalize timestamp of various formats to ISO8601 in UTC (string)."""
    try:
        from datetime import datetime, timezone, timedelta
        if ts_iso is None:
            dt = datetime.now(timezone.utc)
        elif isinstance(ts_iso, (int, float)):
            dt = datetime.utcfromtimestamp(float(ts_iso)).replace(tzinfo=timezone.utc)
        else:
            s = str(ts_iso)
            if s.isdigit():
                dt = datetime.utcfromtimestamp(float(s)).replace(tzinfo=timezone.utc)
            else:
                dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return str(ts_iso)


def _level_from_id(level_id: str|None)->str:
    if not level_id:
        return '-'
    try:
        part = str(level_id).split(':')[-1]
        return ('{:.8f}'.format(float(part))).rstrip('0').rstrip('.')
    except Exception:
        return str(level_id)

def _format_levels(levels, ref_price=None):
    def fmt_price(p):
        try:
            return ("{:.8f}".format(float(p))).rstrip('0').rstrip('.')
        except Exception:
            return str(p)
    d1_items = [L for L in levels if getattr(L,'scope','D1')=='D1' and getattr(L,'kind',None)!='MANUAL' and getattr(L,'price',None) not in (None, "", "-")]
    if ref_price is not None:
        try:
            rp = float(ref_price)
            d1_items.sort(key=lambda L: abs(float(L.price)-rp))
        except Exception:
            pass
    kinds = {getattr(L,'kind','') for L in d1_items}
    if len(kinds) <= 1:
        d1 = [fmt_price(L.price) for L in d1_items]
    else:
        d1 = [f"{L.kind}:{fmt_price(L.price)}" for L in d1_items]
    man = [fmt_price(L.price) for L in levels if getattr(L,'kind',None)=='MANUAL' and getattr(L,'price',None) not in (None, "", "-")]
    return d1, man


def _today_events(log_dir: str, symbols=None) -> List[Dict[str,Any]]:
    path = os.path.join(log_dir, "events.jsonl")
    out_map: Dict[tuple, Dict[str,Any]] = {}
    if not os.path.exists(path):
        return []
    symbols_set = set(symbols or [])
    # Moscow day (UTC+3)
    from datetime import datetime, timedelta, timezone
    MSK = timezone(timedelta(hours=3))
    allowed = {"filtered_out","plan_ready","sweep_detected","plan_extras","risk_block",
               "entry_cancelled_overpenetration","order.place.requested","order.cancel.ok",
               "order.tif_cancelled","trade.entry.opened","trade.exit.closed"}
    with open(path,"r",encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts_raw = rec.get("ts")
            try:
                dt = datetime.fromisoformat(str(ts_raw).replace('Z','+00:00'))
            except Exception:
                # fallback: treat as UTC naive
                dt = datetime.utcnow().replace(tzinfo=timezone.utc)
            if dt.astimezone(MSK).date() != datetime.now(MSK).date():
                continue
            typ = rec.get("type") or rec.get("event")
            sym = rec.get("symbol")
            if typ not in allowed or not sym:
                continue
            if symbols_set and sym not in symbols_set:
                continue
                        # Для событий пробоя фиксируем только первый свип за день по каждому уровню
            if typ == "sweep_detected":
                key = ("sweep", sym, rec.get("level_id"))
                base = out_map.get(key)
                if base is not None:
                    # уже есть запись о пробое этого уровня сегодня
                    continue
                base = dict(rec)
                base["ts"] = dt.isoformat(timespec="seconds")  # нормализованный ISO
                out_map[key] = base
                continue

            dt_min = dt.replace(second=0, microsecond=0)
            key = (dt_min.isoformat(), sym, rec.get('level_id'))  # minute-level dedup для остальных событий
            base = out_map.get(key, {})
            base.update(rec)  # merge extras if arrive later
            base["ts"] = dt.isoformat(timespec="seconds")  # normalized
            out_map[key] = base
    return list(out_map.values())[-200:]


def run(env_path: str = ".env"):
    env = load_env(env_path)
    ms = ModularScanner(env_path=env_path)
    symbols = [_norm_symbol(s.strip()) for s in str(env.get("SYMBOLS","BTC/USDT:USDT")).split(",") if s.strip()]
    w_status = [18, 6, 6, 92, 34]
    w_ev     = [10, 18, 12, 9, 8, 10, 10, 12, 12, 12, 28]
    interval = ms.cfg.scan_interval_sec if hasattr(ms, "cfg") else int(str(env.get("SCAN_INTERVAL_SEC", 5)))

    # 1) Статус по инструментам (один раз)
    print("== Текущий статус по инструментам ==")
    print(_header(["ПАРА","D1","H4","УРОВНИ D1","РУЧНЫЕ УРОВНИ"], w_status))
    for sym in symbols:
        try:
            snap = ms.feed.snapshot(sym)
            d1 = d1_trend(snap["1d"], int(str(env.get("D1_SMA_LEN",200))))
            h4 = h4_trend(snap["4h"], int(str(env.get("H4_SMA_LEN",50))))
            levels = ms.levels.get_levels_d1(sym, snap["1d"],
                                             int(str(env.get("DAYS",10))),
                                             str(env.get("INCLUDE_INSIDE","true")).lower() in ("1","true","yes","on","y"))
            cur = None
            try:
                cur = (snap.get('1m') or snap.get('1h')).close.iloc[-1]
            except Exception:
                try:
                    cur = snap['1h'].close.iloc[-1]
                except Exception:
                    cur = None
            d1_lvls, man_lvls = _format_levels(levels, cur)
            print(_row([sym, d1 or "-", h4 or "-", ", ".join(d1_lvls) or "-", ", ".join(man_lvls) or "-"], w_status))
        except Exception as e:
            # пишем ошибку в JSONL с антифлудом и не засоряем таблицу 2
            try:
                ms.log.event('symbol_error', symbol=sym, error=str(e))
            except Exception:
                pass
            print(_row([sym, "ERR", "", str(e), "-"], w_status))
    print()


    # Determine mode once; use ModularScanner.om as single OrderManager instance
    _env_for_mode = load_env(env_path)
    _mode_live = str(_env_for_mode.get("MODE", "test")).lower() == "live"
    # ms.om is already configured with live flag inside ModularScanner

    # 2) События за сегодня (один раз)
    
    # 2) События за сегодня (ретро из JSONL)
    
    # 2) События за сегодня (ретро из JSONL)
    from usecases.run_tables import print_today_events_table
    log_dir = os.environ.get("LOG_DIR", "logs")
    env_local = load_env(env_path)
    print_today_events_table(log_dir, env_local)

# === онлайн-мониторинг ===
    # шапка онлайн-таблицы событий
    print("== Онлайн события ==")
    print(_header(["ВРЕМЯ","ПАРА","УРОВЕНЬ","D1/H4","СКОР","ГЛУБ","СЕССИЯ","ВХОД","СТОП","ТП","СТАТУС"], w_ev))

    recent: List[Dict[str, Any]] = []
    seen_keys = set()
    while True:
        for sym in symbols:
            try:
                for (L, ev, fres, plan) in ms.step_symbol(sym):
                    # Info-only raw cross event
                    try:
                        if fres and getattr(fres,'extras',None) and fres.extras.get('info_type')=='cross_info':
                            recent.append({
                                'ts': _fmt_utc(fres.extras.get('m1_ts')),
                                'symbol': sym,
                                'level_id': getattr(L, 'id', None),
                                'trend': fres.extras.get('trend', {}),
                                'speed': fres.extras.get('speed', {}),
                                'sessions': fres.extras.get('sessions', []),
                                'depth_atr': float(fres.extras.get('depth_atr_h1') or 0.0),
                                'depth_peak': float(fres.extras.get('depth_atr_h1_max') or 0.0),
                                'type': 'cross_info',
                            })
                            continue
                    except Exception:
                        pass
                    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "symbol": sym,
                           "trend": fres.extras.get("trend",{}),
                           "level_id": getattr(L, "id", None),
                           "speed": fres.extras.get("speed",{}),
                           "sessions": fres.extras.get("sessions",[]),
                           "depth_atr": getattr(ev,"depth_atr_h1",None),
                           "entry": getattr(plan,"entry",None),
                           "stop": getattr(plan,"stop",None),
                           "take": getattr(plan,"take",None),
                           "type": "plan_ready"}
                    # Live mode: place orders now (use scanner's single OrderManager)
                    if _mode_live and ms.om and plan:
                        try:
                            ms.om.place(plan)
                        except Exception as _e:
                            recent.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),"symbol": sym, "type":"error","reasons": "place_failed: "+str(_e)})
                    if not fres.accepted:
                        rec["type"] = "filtered_out"; rec["reasons"] = ",".join(fres.reasons)
                    recent.append(rec)
            except Exception as e:
                recent.append({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "symbol": sym, "type":"error", "reasons": str(e)})
        # печать только новых записей
        while recent:
            r = recent.pop(0)
            t = _fmt_msk(r.get('ts'))
            sym = r.get("symbol","-")
            d1 = (r.get("trend",{}) or {}).get("d1","-")
            h4 = (r.get("trend",{}) or {}).get("h4","-")
            speed = (r.get("speed",{}) or {}).get("ratio","-")
            depth = r.get("depth_atr") or "-"
            sess = ",".join(r.get("sessions",[])) if r.get("sessions") else "-"
            entry = f"{float(r.get('entry',0)):.4f}" if r.get("entry") else "-"
            stop  = f"{float(r.get('stop',0)):.4f}"  if r.get("stop")  else "-"
            take  = f"{float(r.get('take',0)):.4f}"  if r.get("take")  else "-"
            typ = r.get("type")
            # внутренняя ошибка логируем, но не засоряем табличный превью
            if typ == "error":
                continue

            status_map = {
                # 1) пробой
                "cross_info": "1) ПРОБОЙ",
                "sweep_detected": "1) ПРОБОЙ",

                # 2) план/ордер сформирован
                "plan_ready": "2) ПЛАН/ОРДЕР ГОТОВ",
                "order.place.requested": "2) ПЛАН/ОРДЕР ГОТОВ",

                # 3) отказ/отмена
                "filtered_out": "3) ОТКАЗ/ФИЛЬТР",
                "risk_block": "3) ОТКАЗ ПО РИСКУ",
                "reentry_blocked": "3) ПОВТОРНЫЙ ВХОД ЗАПРЕЩЁН",
                "entry_cancelled_overpenetration": "3) ОРДЕР СНЯТ (глубина)",
                "order.cancel.ok": "3) ОРДЕР СНЯТ",
                "order.tif_cancelled": "3) ОРДЕР СНЯТ (TIF)",

                # 4–5 — события по сделкам
                "trade.entry.opened": "4) СДЕЛКА ОТКРЫТА",
                "trade.exit.closed": "5) СДЕЛКА ЗАКРЫТА",
            }
            status = status_map.get(typ, typ or "-")

            # причины отказа добавляем к статусу
            if typ == "filtered_out" and r.get("reasons"):
                status = f"{status}: {r.get('reasons')}"
            if typ in ("sweep_detected", "cross_info"):
                # для пробоя уровня (особенно ручного) считаем достаточно одной строки на цену
                key = (sym, _level_from_id(r.get('level_id')), typ)
            else:
                key = (r.get('ts'), sym, r.get('level_id'), typ)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            print(_row([t, sym, _level_from_id(r.get('level_id')), f"{d1}/{h4}", speed, f"{depth}", sess, entry, stop, take, status], w_ev))
        time.sleep(interval)

def monitor_loop(env_path: str = ".env"):
    return run(env_path)