from __future__ import annotations
from dataclasses import dataclass
from typing import List
from datetime import datetime, timezone

def _iso(ts_int:int)->str:
    try:
        return datetime.fromtimestamp(int(ts_int), tz=timezone.utc).isoformat()
    except Exception:
        return str(ts_int)

from infra.env import load_env, as_int, as_float
from usecases.order_manager import OrderPlan, OrderManager
from .market_feed import MarketFeed
from .level_watcher import LevelWatcher
from .sweep_detector import SweepDetector
from .focus_manager import FocusManager
from .filter_adapter import FilterPipelineAdapter
from .state_store import SignalStateStore
from .audit_logger import AuditLogger

@dataclass
class ScannerConfig:
    scan_interval_sec: int = 5
    atr_h1_len: int = 14
    atr_m5_len: int = 14
    min_atr_frac: float = 0.05
    max_atr_frac: float = 0.35
    return_bars_5m: int = 2
    max_attempts_per_signal: int = 1
    m1_cross_eps_in_ticks: float = 0.5

class ModularScanner:
    def __init__(self, env_path: str = '.env'):
        env = load_env(env_path)
        self.env_path = env_path
        # determine live/test mode once and use single OrderManager instance
        self.mode_live = str(env.get('MODE', 'test')).lower() == 'live'
        self.symbols = [s.strip() for s in str(env.get('SYMBOLS','BTC/USDT:USDT')).split(',') if s.strip()]
        self.cfg = ScannerConfig(
            scan_interval_sec=as_int(env.get('SCAN_INTERVAL_SEC'), 5),
            atr_h1_len=as_int(env.get('ATR_H1_LEN'), 14),
            atr_m5_len=as_int(env.get('ATR_M5_LEN'), 14),
            min_atr_frac=as_float(env.get('MIN_SWEEP_ATR_H1'), 0.10),
            max_atr_frac=as_float(env.get('MAX_SWEEP_ATR_H1'), 0.35),
            return_bars_5m=as_int(env.get('RETURN_BARS_5M'), 2),
            max_attempts_per_signal=1,
            m1_cross_eps_in_ticks=as_float(env.get('M1_CROSS_EPS_IN_TICKS'), 0.5),
        )
        self.feed = MarketFeed(env_path)
        self.levels = LevelWatcher(env_path)
        self.detector = SweepDetector(self.cfg.atr_h1_len, self.cfg.min_atr_frac, self.cfg.max_atr_frac)
        self.focus = FocusManager(self.cfg.atr_m5_len)
        self.filters = FilterPipelineAdapter(env_path)
        self.state = SignalStateStore(env_path=env_path)
        # single OrderManager used for both risk logic and real orders
        self.om = OrderManager(live=self.mode_live, env_path=self.env_path)
        self.log = AuditLogger(to_stdout=False)
        from usecases import fills_hook as _fills_hook
        self._fills_hook = _fills_hook
        # track levels that have already produced a real sweep in this session
        self._disabled_levels = set()

    def ensure_e2e_hooks(self, symbol: str | None = None):
        """E2E hook: poll fills/open orders and trigger SL moves/series accounting."""
        try:
            self._fills_hook.process_fills(self, symbol=symbol)
        except Exception:
            # never break the scan loop because of hook
            pass
        try:
            if symbol:
                self.cancel_overpenetrated_entries(symbol)
        except Exception:
            pass

    
    def cancel_overpenetrated_entries(self, symbol: str) -> None:
        """Cancel entry orders if after ARMED the sweep depth exceeds max ATR(H1).
        Uses latest M1 extremes and current H1 ATR.
        """
        try:
            snap = self.feed.snapshot(symbol)
            h1 = snap['1h']
            m1 = snap['1m']
            if h1 is None or m1 is None or len(h1) < self.cfg.atr_h1_len or len(m1) == 0:
                return
            # compute ATR(H1)
            try:
                from indicators.ta import atr
                import pandas as pd
                h1c = h1.copy()
                h1c['atr'] = atr(h1c, self.cfg.atr_h1_len)
                a = float(h1c['atr'].iloc[-1] or 0.0)
            except Exception:
                a = 0.0
            if not (a and a > 0):
                return
            last = m1.iloc[-1]
            hi = float(last.get('high') or last.get('High') or last.get('H') or last.get('max') or 0.0)
            lo = float(last.get('low') or last.get('Low') or last.get('L') or last.get('min') or 0.0)
            # Iterate ARMED states for this symbol
            for key, st in list(self.state.data.items()):
                if not key.startswith(f"{symbol}|"):
                    continue
                if str(st.get('status')) != 'ARMED':
                    continue
                level_id = key.split('|',1)[1]
                # parse price from level_id "<symbol>:<KIND>:<price>"
                try:
                    parts = level_id.split(':')
                    lvl = float(parts[-1])
                except Exception:
                    continue
                depth = 0.0
                if hi > lvl:
                    depth = hi - lvl
                elif lo < lvl:
                    depth = lvl - lo
                else:
                    depth = 0.0
                frac = (depth / a) if a else 0.0
                if frac > float(self.cfg.max_atr_frac):
                    # Over-penetrated -> cancel entries for this symbol and reset state
                    try:
                        # Best-effort cancel of entry orders for symbol
                        if hasattr(self.om, 'cancel_symbol_entries'):
                            self.om.cancel_symbol_entries(symbol)
                        else:
                            # fall back: just invoke cancel_expired_entries (no-op here) and log
                            self.om.cancel_expired_entries()
                    except Exception:
                        pass
                    # Reset ARMED -> IDLE
                    try:
                        self.state.set_idle(symbol, level_id)
                    except Exception:
                        pass
                    try:
                        self.log.event('entry_cancelled_overpenetration', symbol=symbol, level_id=level_id, level_price=lvl, frac=frac, atr_h1=a)
                    except Exception:
                        pass
        except Exception:
            # never break scan loop
            pass
    def step_symbol(self, symbol: str):
        snap = self.feed.snapshot(symbol)
        d1, h4, h1, m5, m1 = snap['1d'], snap['4h'], snap['1h'], snap['5m'], snap['1m']

        env = load_env(self.env_path)
        days = int(str(env.get('DAYS', 10)))
        include_inside = str(env.get('INCLUDE_INSIDE','true')).lower() in ('1','true','yes','on','y')
        levels = self.levels.get_levels_d1(symbol, d1, days, include_inside)

        last5 = float(m1['close'].iloc[-1])

        # skip levels that have already produced a real sweep in this scanner session
        if getattr(self, "_disabled_levels", None) is None:
            self._disabled_levels = set()

        for L in levels:
            if (symbol, L.id) in self._disabled_levels:
                continue
            if not self.state.can_consider(symbol, L.id):
                continue
            if not self.focus.in_focus(m5, last5, L.price):
                continue

            ev = self.detector.detect(symbol, L, m1, h1)
            if not ev:
                continue

            # strict cross guard removed: now all sweeps pass to M1 revalidation and filters
            # (we keep this block as a no-op to ensure any touch is visible via filtered_out/m1_guard).

            # TABLE2 preview: sweep
            try:
                _t = _iso(getattr(ev, 'm1_bar_ts', 0))
            except Exception:
                _t = str(getattr(ev, 'm1_bar_ts', '?'))
            _lvl = float(getattr(ev, 'level_price', float(getattr(L, 'price'))))
            _side = str(getattr(ev, 'side', '?')).upper()
            _depth = getattr(ev, 'depth_atr_h1', None)

            # try to enrich sweep event with trend / sessions / speed context
            trend_ctx = {}
            sessions_ctx = []
            speed_ctx = {}
            try:
                fres_ctx = self.filters.run(ev, L, d1, h4, h1, ignore_speed=False)
                if getattr(fres_ctx, 'extras', None):
                    trend_ctx = fres_ctx.extras.get('trend', {}) or {}
                    sessions_ctx = fres_ctx.extras.get('sessions', []) or []
                    speed_ctx = fres_ctx.extras.get('speed', {}) or {}
            except Exception:
                # context is optional; never break scan loop because of logging issues
                pass

            # print removed: sweep info now goes via audit log and tables {_t} {symbol} side={_side} level={_lvl} depth_atr={_depth}")
            self.log.event(
                'sweep_detected',
                symbol=symbol,
                level_id=L.id,
                side=ev.side,
                level_price=getattr(ev, 'level_price', float(L.price)),
                depth_atr=ev.depth_atr_h1,
                extreme=ev.extreme_price,
                ts=getattr(ev, 'm1_bar_ts', None),
                trend=trend_ctx,
                sessions=sessions_ctx,
                speed=speed_ctx,
            )

            # --- revalidate against fresh M1 high/low to avoid ghost sweeps ---
            try:
                last_row = m1.iloc[-1]
                hi_check = float(last_row['high'])
                lo_check = float(last_row['low'])
                lvl_check = float(getattr(ev, 'level_price', float(L.price)))
                side_check = str(ev.side).upper()
                # same tick-derived tolerance as above
                tick2 = 0.0
                try:
                    env2 = load_env(self.env_path) if hasattr(self, 'env_path') else load_env(".env")
                    mp2 = (env2.get("TICK_SIZE_MAP") or "")
                    for part in str(mp2).split(","):
                        part = part.strip()
                        if not part or ":" not in part:
                            continue
                        sym, val = part.split(":",1)
                        if sym.strip().upper() == symbol.strip().upper():
                            try:
                                tick2 = float(val)
                            except Exception:
                                tick2 = 0.0
                            break
                except Exception:
                    tick2 = 0.0
                eps = getattr(self.cfg, 'm1_cross_eps_in_ticks', 0.5)
                tol2 = tick2*eps if tick2 and tick2>0 else 0.0
                ok2 = True
                if side_check == "LONG":
                    ok2 = (lo_check < (lvl_check - tol2))
                elif side_check == "SHORT":
                    ok2 = (hi_check > (lvl_check + tol2))
                if not ok2:
                    # treat as filtered-out by M1 guard so it is visible in tables,
                    # but also enrich with trend/speed/session info for today's events table.
                    reason = 'm1_guard:no_real_cross_on_m1'
                    trend_ctx = {}
                    sessions_ctx = []
                    speed_ctx = {}
                    depth_ctx = getattr(ev, 'depth_atr_h1', None)
                    try:
                        # run filters in analytic mode to capture context (no orders will be placed
                        # because we exit early for this level)
                        fres_ctx = self.filters.run(ev, L, d1, h4, h1, ignore_speed=False)
                        if getattr(fres_ctx, 'extras', None):
                            trend_ctx = fres_ctx.extras.get('trend', {}) or {}
                            sessions_ctx = fres_ctx.extras.get('sessions', []) or []
                            speed_ctx = fres_ctx.extras.get('speed', {}) or {}
                            depth_ctx = fres_ctx.extras.get('depth_atr_h1', depth_ctx)
                    except Exception:
                        pass
                    try:
                        self.log.event(
                            'filtered_out', symbol=symbol, level_id=L.id, level_price=lvl_check,
                            reasons=reason, side=side_check, hi=hi_check, lo=lo_check, tol=tol2,
                            trend=trend_ctx, sessions=sessions_ctx, speed=speed_ctx,
                            depth_atr=depth_ctx, depth_atr_h1=depth_ctx,
                        )
                    except Exception:
                        pass
                    # yield stub result for online table and skip further processing for this level
                    class _Stub:
                        def __init__(self, reasons, extras):
                            self.accepted = False
                            self.reasons = reasons
                            self.extras = extras
                    extras = {
                        'm1_guard': {'ok': False, 'hi': hi_check, 'lo': lo_check, 'tol': tol2},
                        'trend': trend_ctx,
                        'sessions': sessions_ctx,
                        'speed': speed_ctx,
                    }
                    yield (L, ev, _Stub([reason], extras), None)
                    continue
            except Exception:
                pass

            # debug print (pair, time, level) removed: info is available via audit log and tables
            try:
                print(f"{symbol} | {_iso(getattr(ev,'m1_bar_ts',0))} | {getattr(ev,'level_price', float(L.price))}")
            except Exception:
                pass
            self.state.set_armed(symbol, L.id, self.cfg.return_bars_5m)
            # after a real sweep, disable this level from further consideration in this session
            try:
                self._disabled_levels.add((symbol, L.id))
            except Exception:
                pass
            st = self.state.get(symbol, L.id)
            attempts = int(st.get('attempts', 0))
            # first attempt for filters: attempts==0 means this is the first sweep for this level
            at = attempts + 1
            ignore_speed = False


            if at <= 1:
                fres = self.filters.run(ev, L, d1, h4, h1, ignore_speed=ignore_speed)
                # enrich filter extras with signal state / window info for transparency
                try:
                    st = self.state.get(symbol, L.id)
                    extras = getattr(fres, "extras", {}) or {}
                    extras_state = {
                        "status": st.get("status"),
                        "attempts": int(st.get("attempts", 0)),
                        "return_bars_5m": int(self.cfg.return_bars_5m),
                        "return_deadline_iso": st.get("deadline_iso"),
                    }
                    # avoid clobbering if already present
                    prev_state = extras.get("state", {})
                    if isinstance(prev_state, dict):
                        prev_state.update({k: v for k, v in extras_state.items() if v is not None})
                        extras_state = prev_state
                    extras["state"] = extras_state
                    fres.extras = extras
                except Exception:
                    pass
                # If filters rejected the signal or speed explicitly failed, log filtered_out
                try:
                    extras = getattr(fres, "extras", {}) or {}
                    reasons = getattr(fres, "reasons", None)
                    accepted = getattr(fres, "accepted", None)
                    speed_info = extras.get("speed", {}) or {}
                    speed_ok = speed_info.get("ok", True)
                    has_reasons = False
                    if isinstance(reasons, (list, tuple, set)):
                        has_reasons = len(reasons) > 0
                    elif isinstance(reasons, str):
                        has_reasons = bool(reasons.strip())
                    if (accepted is False) or has_reasons or (isinstance(speed_info, dict) and speed_ok is False):
                        _reasons = reasons
                        _rs = ', '.join(_reasons) if isinstance(_reasons, (list, tuple, set)) else str(_reasons)
                        try:
                            _lvl = float(getattr(ev, 'level_price', float(getattr(L, 'price'))))
                        except Exception:
                            _lvl = getattr(L, 'price', None)
                        depth_ctx = getattr(ev, 'depth_atr_h1', None)
                        self.log.event(
                            'filtered_out',
                            symbol=symbol,
                            level_id=L.id,
                            level_price=float(getattr(L, 'price')),
                            reasons=','.join(_reasons) if isinstance(_reasons, (list, tuple, set)) else str(_reasons),
                            trend=extras.get('trend', {}),
                            sessions=extras.get('sessions', []),
                            speed=speed_info,
                            depth_atr=depth_ctx,
                            depth_atr_h1=depth_ctx,
                        )
                        continue
                except Exception:
                    # logging issues must not affect trading logic
                    pass

# Build order plan via OrderManager.make_plan (entry/SL/TP/qty) per env settings
            entry_ticks = as_int(env.get('ENTRY_TICKS'), 2)
            stop_ticks = as_int(env.get('STOP_TICKS'), 2)
            stop_by_sweep = str(env.get('STOP_BY_SWEEP','true')).lower() in ('1','true','yes','on','y')
            rr = as_float(env.get('RR'), 3.0)
            tif_minutes = int(self.cfg.return_bars_5m) * 5  # window in minutes
            direction = str(ev.side).upper()  # LONG or SHORT
            plan = self.om.make_plan(
                symbol=symbol,
                direction=direction,
                level_price=float(L.price),
                sweep_extreme=float(getattr(ev,'extreme_price', L.price)),
                entry_ticks=entry_ticks,
                stop_by_sweep=stop_by_sweep,
                stop_ticks=stop_ticks,
                rr=rr,
                tif_minutes=tif_minutes,
            )
            side = plan.side
            entry, stop, take = plan.entry, plan.stop, plan.take
            qty = plan.qty

            # mark time of return into acceptable range for analytics
            try:
                rt_sec = self.state.record_return(symbol, L.id)
                self.log.event(
                    'return_to_range',
                    symbol=symbol,
                    level_id=L.id,
                    level_price=float(getattr(L, 'price')),
                    return_time_sec=rt_sec,
                )
            except Exception:
                # logging/metrics issues must not affect trading
                pass

            # --- RISK GATE: max open trades & stop-streak ---
            max_open = as_int(env.get('MAX_OPEN_TRADES'), 1)
            if self.om.is_stop_streak_blocked():
                self.log.event('risk_block', symbol=symbol, level_id=L.id, reason='stop_streak')
                continue
            try:
                open_cnt = self.om.open_trades_count()
            except Exception:
                open_cnt = 0
            if open_cnt >= max_open:
                self.log.event('risk_block', symbol=symbol, level_id=L.id, reason='max_open_trades', open=open_cnt, max=max_open)
                continue

            # (using plan from make_plan)
            # (using plan from make_plan)
            # (using plan from make_plan)
            # (using plan from make_plan)
            # TABLE2 preview: plan ready
            try:
                _lvl = float(getattr(ev,'level_price', float(getattr(L,'price'))))
            except Exception:
                _lvl = getattr(L,'price', None)
            # print removed: plan info now goes via audit log and tables {symbol} level={_lvl} entry={plan.entry} stop={plan.stop} take={plan.take}")
            self.log.event('plan_ready', symbol=symbol, level_id=L.id, side=side, entry=entry, stop=stop, take=take, qty=qty, attempt=at)
            
            # recompute depth_atr_h1 using latest M1 extreme up to now
            try:
                lvl_px = float(getattr(L,'price', 0.0))
                _m1_last = m1.iloc[-1]
                _hi = float(_m1_last.get('high') or _m1_last.get('HI') or _m1_last.get('h') or 0.0)
                _lo = float(_m1_last.get('low') or _m1_last.get('LO') or _m1_last.get('l') or 0.0)
                _base_ext = float(getattr(ev,'extreme_price', _lo if str(ev.side).upper()=='LONG' else _hi) or 0.0)
                _cur_ext = min(_base_ext, _lo) if str(ev.side).upper()=='LONG' else max(_base_ext, _hi)
                _a = atr(h1, self.cfg.atr_h1_len)
                _atr = float(_a.iloc[-1] or 0.0) if _a is not None and len(_a)>0 else 0.0
                depth_atr_h1_new = (abs(_cur_ext - lvl_px) / _atr) if _atr else getattr(ev,'depth_atr_h1', None)
            except Exception:
                depth_atr_h1_new = getattr(ev,'depth_atr_h1', None)
            self.log.event('plan_extras', symbol=symbol, level_id=L.id, trend_d1=d1, trend_h4=h4, depth_atr_h1=depth_atr_h1_new, side=ev.side, entry=entry, stop=stop, take=take)
            # depth-in-range confirmation (succeeds filters, ready for planning)
            try:
                self.log.event(
                    'depth_range_ok',
                    symbol=symbol,
                    level_id=L.id,
                    signal_id=_make_signal_id(symbol, L.id),
                    side=ev.side,
                    level_price=float(getattr(L,'price')),
                    depth_atr=depth_atr_h1_new,
                    depth_atr_h1=depth_atr_h1_new,
                )
            except Exception:
                pass


            
            # === Informational cross tracking (does not affect detector logic) ===
            try:
                _m1_last = m1.iloc[-1]
                _hi = float(_m1_last.get('high') or _m1_last.get('HI') or _m1_last.get('h') or 0.0)
                _lo = float(_m1_last.get('low') or _m1_last.get('LO') or _m1_last.get('l') or 0.0)
                try:
                    _tick = 0.0
                    if self.om and hasattr(self.om, 'tick_size'):
                        _tick = float(self.om.tick_size(symbol) or 0.0)
                except Exception:
                    _tick = 0.0
                _tol = _tick/2.0 if _tick and _tick>0 else 0.0
                _lvl = float(getattr(L,'price', 0.0))
                _side = "LONG" if _lo < (_lvl - _tol) else ("SHORT" if _hi > (_lvl + _tol) else None)
                if _side:
                    from indicators.ta import atr
                    _a = atr(h1, self.cfg.atr_h1_len)
                    _atr = float(_a.iloc[-1] or 0.0) if _a is not None and len(_a)>0 else 0.0
                    if _atr > 0:
                        _ext = float(_lo if _side=='LONG' else _hi)
                        _cur = abs(_ext - _lvl) / _atr
                        _key = (symbol, L.id)
                        _peak_prev = getattr(self, '_peaks', {}).get(_key, 0.0)
                        _peak = _cur if _cur > _peak_prev else _peak_prev
                        self._peaks[_key] = _peak
                        info = {
                            'info_type': 'cross_info',
                            'side': _side,
                            'depth_atr_h1': _cur,
                            'depth_atr_h1_max': _peak,
                            'extreme_price': _ext,
                            'm1_ts': int(SweepDetector._ts_to_int(_m1_last.get('ts') or _m1_last.get('timestamp') or _m1_last.get('time'))),
                        }
                        try:
                            self.log.event('cross_info', symbol=symbol, level_id=L.id, **info)
                        except Exception:
                            pass
                        class _Stub:
                            def __init__(self, extras): self.extras = extras; self.accepted=False; self.reasons=[]
                        yield (L, None, _Stub(info), None)
            except Exception:
                pass

            yield (L, ev, fres, plan)

            # E2E fills hook per symbol
            self.ensure_e2e_hooks(symbol)
