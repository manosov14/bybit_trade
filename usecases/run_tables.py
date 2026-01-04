
from __future__ import annotations
import os, json, gzip, glob, math
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List

DATE_FMT = "%Y-%m-%d"
TS_FMT = "%H:%M:%S"

try:
    from zoneinfo import ZoneInfo
except Exception:
    # Fallback for very old Python (shouldn't happen in prod)
    class ZoneInfo:
        def __init__(self, key):
            self.key = key

def _get_local_tz(env: dict | None) -> "ZoneInfo":
    # Read timezone from env dict or process env; default to Europe/Berlin for backward compatibility
    tz_name = None
    if isinstance(env, dict):
        tz_name = (str(env.get("TIMEZONE", "")).strip() or None)
    if not tz_name:
        tz_name = os.environ.get("TIMEZONE", "").strip() or "Europe/Berlin"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        try:
            return ZoneInfo("UTC")
        except Exception:
            # Extremely defensive fallback — emulate naive UTC
            class _UTC:
                def __repr__(self): return "UTC"
            return _UTC()

try:
    from colorama import init as _cinit, Fore, Style
    _cinit(); _USE_COLORS = True
except Exception:
    class _Stub: GREEN=RED=YELLOW=CYAN=RESET=""
    Fore = Style = _Stub(); _USE_COLORS = False

def _c(s, color): return f"{color}{s}{Style.RESET_ALL}" if _USE_COLORS else str(s)
def _pad(s, w): s = "" if s is None else str(s); return (s[:w-1]+"…") if len(s)>w else s.ljust(w)
def _fmt_ts(ts):
    if ts is None: return "-"
    try:
        if isinstance(ts,(int,float)): from datetime import datetime; return datetime.utcfromtimestamp(float(ts)).strftime(TS_FMT)
        return datetime.fromisoformat(str(ts).replace("Z","+00:00")).strftime(TS_FMT)
    except: return str(ts)
def _fmt_price(x):
    try:
        f=float(x); return f"{f:.0f}" if abs(f)>=100 else f"{f:.4f}"
    except: return str(x)

# NOTE: env made optional for backward compatibility with existing callers
def _iter_today_events(log_dir:str, env:dict | None = None):
    local_tz = _get_local_tz(env)
    today = datetime.now(local_tz).strftime(DATE_FMT)
    paths = [os.path.join(log_dir,"events.jsonl")] + sorted(glob.glob(os.path.join(log_dir,"events-*.jsonl*")))
    for p in paths:
        if not os.path.exists(p): continue
        try:
            it = (gzip.open(p,"rt",encoding="utf-8",errors="ignore") if p.endswith(".gz") else open(p,"r",encoding="utf-8",errors="ignore"))
            with it as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln: continue
                    try: obj = json.loads(ln)
                    except: continue
                    ts = obj.get("ts") or obj.get("time") or obj.get("timestamp")
                    if not ts: continue
                    try: d = datetime.fromisoformat(str(ts).replace("Z","+00:00")).astimezone(local_tz).strftime(DATE_FMT)
                    except:
                        try: d = datetime.fromtimestamp(float(ts), tz=local_tz).strftime(DATE_FMT)
                        except: continue
                    if d == today: yield obj
        except: continue

def _merge_events(rows, window_sec:int=120):
    def _tsec(ts):
        if ts is None: return None
        try:
            if isinstance(ts,(int,float)): return int(float(ts))
            return int(datetime.fromisoformat(str(ts).replace("Z","+00:00")).timestamp())
        except: return None
    rank={"plan_ready":3,"filtered_out":2,"sweep_detected":1}
    groups=[]
    for obj in rows:
        ev=(obj.get("event") or obj.get("type") or obj.get("status") or "").strip()
        if not obj.get("symbol") and not obj.get("pair"): continue
        sym = obj.get("symbol") or obj.get("pair")
        # level_id for scanner events, trade_id for order/trade lifecycle events
        lid = (
            obj.get("level_id")
            or (obj.get("plan") or {}).get("level_id")
            or obj.get("trade_id")
            or obj.get("order_id")
            or obj.get("clientOrderId")
        )
        if not sym or not lid: continue
        ts = obj.get("ts") or obj.get("time") or obj.get("timestamp")
        tsec = _tsec(ts)
        if tsec is None: continue
        gi=None
        for i in range(len(groups)-1,-1,-1):
            g=groups[i]
            if g["symbol"]==sym and g["level_id"]==lid and abs(tsec-_tsec(g["ts"]))<=window_sec:
                gi=i; break
        if gi is None:
            g={"ts":ts,"symbol":sym,"level_id":lid,"_rank":rank.get(ev,0)}; groups.append(g)
        else:
            g=groups[gi]
            if tsec>_tsec(g["ts"]): g["ts"]=ts
        for k in (
            "side",
            "level_price",
            "depth_atr",
            "depth_atr_h1",
            "entry",
            "stop",
            "take",
            "qty",
            "reasons",
            "session",
            "sessions",
            "result",
            "raw_result",
            "pnl",
            "rr",
            "planned_rr",
            "risk_pct",
            "return_time_sec",
        ):
            if obj.get(k) is not None:
                g[k] = obj.get(k)
        if obj.get("trend_d1") or obj.get("trend_h4"):
            g["trend_d1"]=obj.get("trend_d1"); g["trend_h4"]=obj.get("trend_h4")
        if isinstance(obj.get("trend"),dict) or isinstance(obj.get("trends"),dict):
            t = obj.get("trend") or obj.get("trends") or {}
            # поддерживаем разные варианты ключей из контекста тренда
            g["trend_d1"] = g.get("trend_d1") or (
                t.get("d1")
                or t.get("D1")
                or t.get("d1_trend")
                or t.get("d1_dir")
            )
            g["trend_h4"] = g.get("trend_h4") or (
                t.get("h4")
                or t.get("H4")
                or t.get("h4_trend")
                or t.get("h4_dir")
            )
        if isinstance(obj.get("speed"),dict):
            g["speed"]={**g.get("speed",{}), **obj["speed"]}
        cur=g.get("_status_ev")
        if ev and (cur is None or rank.get(ev,0)>=rank.get(cur,0)): g["_status_ev"]=ev
    out=[]
    for g in groups: g["event"]=g.pop("_status_ev",None); out.append(g)
    out.sort(key=lambda x: str(x.get("ts"))); return out

def _localize_status(ev:str)->str:
    if not ev: return "-"
    ev=str(ev).lower()
    if "plan_ready" in ev or "plan ready" in ev or ev=="plan": return "план готов"
    if "filtered_out" in ev or "filtered" in ev or "reject" in ev: return "отфильтровано"
    if "sweep_detected" in ev or "sweep" in ev or "break" in ev or "прокол" in ev: return "прокол"
    return ev

def print_today_events_table(log_dir:str, env:dict)->None:
    rows=list(_iter_today_events(log_dir, env))
    rows=[r for r in rows if (r.get("symbol") or r.get("pair"))]
    print("=== События за сегодня ===")
    if not rows:
        print("Пока нет событий за сегодня."); return
    window=int(env.get("MERGE_WINDOW_SECONDS",120) or 120)
    rows=_merge_events(rows, window_sec=window)
    headers=["ПАРА","ВРЕМЯ (UTC)","НАПР","УРОВЕНЬ","D1/H4","СКОРОСТЬ","ГЛУБ. (ATR H1)","СЕССИЯ","ВХОД","СТОП","ТП","ВОЗВРАТ (СЕК)","PnL","RR","Риск %","СТАТУС"]
    widths =[12,10,6,12,9,9,14,12,10,10,10,12,10,8,9,18]
    print(" | ".join(_pad(h,w) for h,w in zip(headers,widths)))
    for obj in rows:
        sym=(obj.get("symbol") or obj.get("pair") or "-"); sym_disp = _c(sym, Fore.CYAN)
        t=_fmt_ts(obj.get("ts"))
        side=(obj.get("side") or "-").lower()
        side_disp = _c("long",Fore.GREEN) if side=="long" else (_c("short",Fore.RED) if side=="short" else side)
        level=obj.get("level_price"); level_disp = "-" if level is None else _fmt_price(level)
        d1=obj.get("trend_d1") or "-"; h4=obj.get("trend_h4") or "-"; d1h4=f"{d1}/{h4}"
        sp=obj.get("speed") or {}
        if isinstance(sp,dict):
            if sp.get("ignored"): speed_disp="off"
            else:
                val=sp.get("ratio") or sp.get("z") or sp.get("zscore")
                try: speed_disp=f"{float(val):.2f}"
                except: speed_disp=str(val or "-")
        else: speed_disp="-"
        depth=obj.get("depth_atr_h1") or obj.get("depth_atr")
        if depth is None: depth_disp="-"
        else:
            try:
                dv=float(depth)
                if dv<0.15: depth_disp=_c(f"{dv:.2f}",Fore.YELLOW)
                elif dv<=0.35: depth_disp=_c(f"{dv:.2f}",Fore.GREEN)
                else: depth_disp=_c(f"{dv:.2f}",Fore.RED)
            except: depth_disp=str(depth)
        sess=obj.get("session") or obj.get("sessions")
        if isinstance(sess,(list,tuple)): sess_disp=", ".join(map(str,sess)) if sess else "-"
        else: sess_disp=sess or "-"
        entry=obj.get("entry"); stop=obj.get("stop"); take=obj.get("take")
        entry_disp="-" if entry is None else _fmt_price(entry)
        stop_disp ="-" if stop  is None else _fmt_price(stop)
        take_disp ="-" if take  is None else _fmt_price(take)

        # return time in seconds (from penetration to return-to-range), if available
        rt_val = obj.get("return_time_sec")
        if rt_val is None:
            return_disp = "-"
        else:
            try:
                return_disp = f"{float(rt_val):.1f}"
            except Exception:
                return_disp = str(rt_val)

        # outcome metrics: PnL, RR, risk %
        pnl_val = obj.get("pnl")
        rr_val = obj.get("rr") or obj.get("rr_realized") or obj.get("planned_rr")
        risk_pct = obj.get("risk_pct")

        def _fmt_num(v, digits=2):
            if v is None:
                return "-"
            try:
                return f"{float(v):.{digits}f}"
            except Exception:
                return str(v)

        pnl_disp = _fmt_num(pnl_val, digits=2)
        rr_disp = _fmt_num(rr_val, digits=2)
        risk_disp = _fmt_num(risk_pct, digits=2) if risk_pct is not None else "-"

        st_raw = obj.get("event") or obj.get("type") or obj.get("status") or "-"

        def reasons_text(r):
            if r is None:
                return "-"
            if isinstance(r, (list, tuple, set)):
                return ", ".join(map(str, r))
            if isinstance(r, dict):
                return ", ".join(f"{k}:{v}" for k, v in r.items())
            return str(r)

        reasons = obj.get("reasons")
        st = str(st_raw)
        base_disp = _localize_status(st)

        # human-friendly status text (без PnL/RR/риска — только "что случилось")
        if st == "filtered_out" and reasons is not None:
            st_disp = "отфильтровано: " + reasons_text(reasons)
        elif st.startswith("trade.exit"):
            # Сделка закрыта: отдельно покажем только тип результата (стоп/профит)
            if rr_val is not None or pnl_val is not None:
                # result может быть "stop"/"profit"/"sl"/"tp"
                result = obj.get("result") or obj.get("raw_result")
                if result is not None:
                    r_str = str(result).lower()
                    if r_str in ("sl", "stop") or "stop" in r_str:
                        res_ru = "стоп"
                    elif r_str in ("tp", "take") or "profit" in r_str:
                        res_ru = "профит"
                    else:
                        res_ru = str(result)
                    st_disp = f"{base_disp}, {res_ru}"
                else:
                    st_disp = base_disp
            else:
                st_disp = base_disp
        else:
            st_disp = base_disp

        # colorization
        if st in ("plan_ready", "plan") or st_disp == "план готов":
            status_disp = _c(st_disp, Fore.GREEN)
        elif st in ("sweep_detected", "прокол"):
            status_disp = _c(st_disp, Fore.CYAN)
        elif st in ("filtered_out", "отфильтровано") or str(st).startswith("filtered"):
            status_disp = _c(st_disp, Fore.YELLOW)
        elif st.startswith("trade.exit"):
            # green for profit, red for stop, default otherwise
            result = obj.get("result") or obj.get("raw_result")
            r_str = (str(result).lower() if result is not None else "")
            if r_str in ("tp", "take") or "profit" in r_str:
                status_disp = _c(st_disp, Fore.GREEN)
            elif r_str in ("sl", "stop"):
                status_disp = _c(st_disp, Fore.RED)
            else:
                status_disp = st_disp
        else:
            status_disp = st_disp

        line=[sym_disp,t,side_disp,level_disp,d1h4,speed_disp,depth_disp,sess_disp,entry_disp,stop_disp,take_disp,return_disp,pnl_disp,rr_disp,risk_disp,status_disp]
        print(" | ".join(_pad(s,w) for s,w in zip(line,widths)))

def print_symbols_snapshot(state_path:str, env:dict)->None:
    p=Path(state_path)
    if not p.exists(): print("Снимок отсутствует."); return
    try: state=json.loads(p.read_text(encoding="utf-8"))
    except Exception: print("(ошибка чтения state.json)"); return
    trades=state.get("trades") or {}
    if not trades: print("Активных сигналов нет."); return
    headers=["ПАРА","БЛИЖ. УРОВЕНЬ","Δ/ATR(M5)","ФОКУС","СТАТУС","ПОПЫТКИ","RE-ENTRY ДО","ВОЗВРАТ ДО","СКОРОСТЬ","D1/H4","LOCKOUT"]
    widths =[12,14,10,7,10,8,19,19,10,9,10]
    print(" | ".join(_pad(h,w) for h,w in zip(headers,widths)))
    for _,st in trades.items():
        sym=st.get("symbol","-")
        level=st.get("nearest_level") or "-"
        try: delta=f"{float(st.get('delta_atr_m5') or 0):.2f}"
        except: delta="-"
        focus="ДА" if st.get("in_focus") else "НЕТ"
        attempts=st.get("attempts") or 0
        reentry_until=st.get("reentry_deadline") or "-"
        return_until=st.get("deadline") or st.get("return_deadline") or "-"
        speed_on=st.get("speed_enabled",True)
        speed_disp=_c("вкл",Fore.GREEN) if speed_on else _c("выкл",Fore.YELLOW)
        trend=f"{st.get('trend_d1','-')}/{st.get('trend_h4','-')}"
        lock=st.get("lockout_reason","-")
        status=st.get("status","-")
        st_col=status
        low=str(status).lower()
        if "активен" in low or "повтор" in low: st_col=_c(status,Fore.GREEN)
        elif "блок" in low or "таймаут" in low: st_col=_c(status,Fore.YELLOW)
        elif "отмен" in low: st_col=_c(status,Fore.YELLOW)
        line=[sym,str(level),str(delta),focus,st_col,str(attempts),str(reentry_until),str(return_until),speed_disp,str(trend),str(lock)]
        print(" | ".join(_pad(s,w) for s,w in zip(line,widths)))


def _localize_status(ev: str) -> str:
    if not ev:
        return "-"
    ev = str(ev)
    if 'plan_canceled' in ev or 'cancel' in ev:
        return 'заявка снята'
    if 'trade_opened' in ev or 'open' in ev:
        return 'сделка открыта'
    if 'trade_closed' in ev or 'closed' in ev:
        return 'сделка закрыта'
    if 'plan_ready' in ev:
        return 'план готов'
    if 'filtered_out' in ev:
        return 'отфильтровано'
    if 'sweep_detected' in ev or 'sweep' in ev:
        return 'прокол'
    return ev
