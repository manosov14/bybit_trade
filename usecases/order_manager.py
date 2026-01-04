
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
import math
from datetime import datetime, timezone, timedelta

from infra.env import load_env


import json, os, hashlib
from datetime import datetime, timezone

_LOG_DIR = os.environ.get("LOG_DIR", "logs")
os.makedirs(_LOG_DIR, exist_ok=True)
_EVENTS_PATH = os.path.join(_LOG_DIR, "events.jsonl")
_STATE_PATH = os.path.join(_LOG_DIR, "state.json")

def _jlog(event: str, **fields):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
    for k,v in fields.items():
        try:
            json.dumps({k:v})
            rec[k]=v
        except Exception:
            rec[k]=str(v)
    try:
        with open(_EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _load_state() -> dict:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"trades": {}}

def _save_state(state: dict) -> None:
    tmp = _STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _STATE_PATH)

def _plan_fingerprint(plan) -> str:
    s = f"{plan.symbol}|{plan.side}|{plan.entry}|{plan.stop}|{plan.take}|{plan.qty}"
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]
# NOTE: We avoid hard coupling to ccxt so tests can run offline.
try:
    from infra.exchange_ccxt import ExchangeService  # alias to infra.exchange.ExchangeService
except Exception:
    ExchangeService = object  # type: ignore


def _to_bool(x: Any, default: bool=False) -> bool:
    if x is None:
        return default
    s = str(x).strip().lower()
    if s in {"1","true","yes","y","on"}:
        return True
    if s in {"0","false","no","n","off"}:
        return False
    return default


def _parse_map(s: Optional[str]) -> Dict[str, float]:
    """
    Parse 'BTC/USDT:USDT:0.1,ETH/USDT:USDT:0.01' -> { 'BTC/USDT:USDT': 0.1, ... }
    Accepts either 'symbol:value' or 'symbol:value' pairs separated by commas.
    """
    out: Dict[str, float] = {}
    if not s:
        return out
    for part in str(s).split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        sym, val = part.split(":", 1)
        try:
            out[sym.strip().upper()] = float(str(val).strip())
        except Exception:
            continue
    return out


@dataclass
class OrderPlan:
    symbol: str
    side: str                      # 'BUY' or 'SELL'
    entry: float                   # stop trigger price (stop-market or stop-limit)
    stop: float                    # protective stop loss price
    take: float                    # take profit price
    qty: float                     # base size
    rr: float                      # risk/reward (for reference)
    time_in_force: str = "GTC"     # or 'IOC' etc.
    tif_deadline: Optional[datetime] = None  # when to auto-cancel in test mode


@dataclass
class OrderIds:
    entry_id: Optional[str]
    stop_id: Optional[str]
    take_id: Optional[str]



    def _sync_by_client_id_prefix(self, symbol: str, tid_prefix: str) -> Optional[OrderIds]:
        """Search open orders for clientOrderId starting with tid_prefix and update state."""
        try:
            opens = self.ex.ex.fetch_open_orders(symbol=symbol) if (self.live and self.ex) else []
        except Exception:
            opens = []
        entry_id = stop_id = take_id = None
        for o in opens or []:
            coid = str(o.get('clientOrderId') or '')
            oid = str(o.get('id') or '')
            if not coid.startswith(tid_prefix):
                continue
            if coid.endswith('-entry'):
                entry_id = oid
            elif coid.endswith('-sl'):
                stop_id = oid
            elif coid.endswith('-tp'):
                take_id = oid
        if any([entry_id, stop_id, take_id]):
            try:
                st = _load_state(); trades = st.setdefault('trades', {})
                tr = trades.get(tid_prefix) or {}
                tr['symbol'] = symbol
                tr['order_ids'] = {'entry': entry_id, 'sl': stop_id, 'tp': take_id}
                trades[tid_prefix] = tr
                _save_state(st)
            except Exception:
                pass
            return OrderIds(entry_id=entry_id, stop_id=stop_id, take_id=take_id)
        return None

class OrderManager:
    """
    Computes orders (entry, SL, TP) from risk parameters and optionally places them.
    Works in two modes:
      - live=True  -> uses ExchangeService (ccxt) to send real orders
      - live=False -> prints actions and returns fake IDs for testing
    """
    # ---- stop-streak helpers (TP resets streak) ----
    def _stop_streak_info(self) -> dict:
        st = _load_state()
        return st.setdefault("stop_streak", {"count": 0, "cooldown_until": None})

    def clear_stop_streak(self) -> None:
        st = _load_state()
        st["stop_streak"] = {"count": 0, "cooldown_until": None}
        _save_state(st)
        _jlog("risk.stop_streak.clear")

    
    def record_stop_loss_event(self) -> None:
        """Call this when SL is hit.
        Increments stop-streak counter and, if it reaches STOP_SERIES_LIMIT, sets a cooldown.
        """
        try:
            st = _load_state()
            info = st.setdefault("stop_streak", {"count": 0, "cooldown_until": None})
            try:
                limit = int(str(self.env.get("STOP_SERIES_LIMIT", 3)))
            except Exception:
                limit = 3
            try:
                cooldown_h = int(str(self.env.get("STOP_SERIES_COOLDOWN_HOURS", 12)))
            except Exception:
                cooldown_h = 12
            count = int(info.get("count", 0)) + 1
            info["count"] = count
            if count >= limit:
                try:
                    from datetime import datetime, timezone, timedelta
                    info["cooldown_until"] = (datetime.now(timezone.utc) + timedelta(hours=cooldown_h)).isoformat()
                except Exception:
                    info["cooldown_until"] = None
            st["stop_streak"] = info
            _save_state(st)
            _jlog("risk.stop_streak.inc", count=count, limit=limit, cooldown_until=info.get("cooldown_until"))
        except Exception:
            # non-fatal
            pass


    def poll_and_book_outcomes(self) -> None:
        """Detect SL/TP outcomes, update risk streaks and log rich exit events.

        This reads recent closed orders from the exchange, matches them to our
        internal trades by clientOrderId prefix, computes PnL and realized RR,
        and logs a detailed "trade.exit.closed" event into events.jsonl.
        No-op in test mode. Safe to call frequently.
        """
        if not self.live or self.ex is None:
            # test mode: nothing to poll
            return

        try:
            # Heuristic: scan recently closed orders and look at clientOrderId suffix
            # to infer whether SL or TP was hit.
            closed = []
            try:
                closed = self.ex.ex.fetch_closed_orders()
            except Exception:
                try:
                    closed = self.ex.ex.fetch_my_trades()
                except Exception:
                    closed = []

            if not closed:
                return

            st = _load_state()
            trades = st.setdefault("trades", {})
            seen = st.setdefault("seen_closures", {})

            for o in closed or []:
                coid = str(o.get("clientOrderId") or "")
                oid = str(o.get("id") or "")
                key = coid or oid
                if not key or seen.get(key):
                    continue
                seen[key] = True

                sfx = ""
                if coid.endswith("-sl"):
                    sfx = "sl"
                elif coid.endswith("-tp"):
                    sfx = "tp"
                elif coid.endswith("-entry"):
                    sfx = "entry"

                if sfx not in ("sl", "tp", "entry"):
                    continue

                try:
                    symbol = o.get("symbol")
                    side = str(o.get("side") or "").upper()
                    price = (
                        o.get("price")
                        or o.get("average")
                        or o.get("avgPrice")
                        or (o.get("info", {}) or {}).get("avgPrice")
                    )
                except Exception:
                    symbol = o.get("symbol")
                    side = str(o.get("side") or "").upper()
                    price = None

                # match to our trade by clientOrderId prefix
                tid = coid.rsplit("-", 1)[0] if coid and "-" in coid else None
                trade = trades.get(tid) if tid else None

                entry = float(trade.get("entry")) if trade and trade.get("entry") is not None else None
                stop = float(trade.get("stop")) if trade and trade.get("stop") is not None else None
                take = float(trade.get("take")) if trade and trade.get("take") is not None else None
                qty = float(trade.get("qty")) if trade and trade.get("qty") is not None else None
                risk_pct = float(trade.get("risk_pct")) if trade and trade.get("risk_pct") is not None else float(self.risk_pct)
                planned_rr = float(trade.get("rr")) if trade and trade.get("rr") is not None else None

                # entry fill: log opening of the trade when entry stop is triggered
                if sfx == "entry":
                    try:
                        now = datetime.now(timezone.utc)
                    except Exception:
                        now = None
                    if trade is not None and tid:
                        if now is not None:
                            trade.setdefault("opened_at", now.isoformat())
                        if price is not None:
                            try:
                                trade["entry_exec"] = float(price)
                            except Exception:
                                trade["entry_exec"] = price
                        trades[tid] = trade
                    _jlog(
                        "trade.entry.opened",
                        symbol=symbol,
                        side=side,
                        entry_price=price,
                        trade_id=tid,
                        signal_id=(trade.get("signal_id") if isinstance(trade, dict) else None),
                        qty=qty,
                        risk_pct=risk_pct,
                        planned_rr=planned_rr,
                    )
                    # skip exit handling for pure entry fills
                    continue

                result_type = "stop" if sfx == "sl" else "profit"

                pnl = None
                rr_realized = None
                try:
                    if entry is not None and qty is not None and price is not None:
                        price_f = float(price)
                        side_dir = 1.0 if side == "BUY" else -1.0
                        pnl = (price_f - entry) * qty * side_dir
                        risk_money = self.account_equity * (risk_pct / 100.0)
                        if risk_money > 0:
                            rr_realized = pnl / risk_money
                        price = price_f
                except Exception:
                    pass

                # mark trade as closed in state
                if trade is not None and tid:
                    trade["closed"] = True
                    trade["closed_at"] = datetime.now(timezone.utc).isoformat()
                    trade["exit_price"] = float(price) if price is not None else None
                    trade["result"] = result_type
                    trade["pnl"] = pnl
                    trade["rr_realized"] = rr_realized
                    trades[tid] = trade

                _jlog(
                    "trade.exit.closed",
                    symbol=symbol,
                    side=side,
                    exit_price=price,
                    result=result_type,
                    raw_result=sfx,
                    order_id=oid,
                    clientOrderId=coid,
                    trade_id=tid,
                    signal_id=(trade.get("signal_id") if isinstance(trade, dict) else None),
                    entry=entry,
                    stop=stop,
                    take=take,
                    qty=qty,
                    risk_pct=risk_pct,
                    planned_rr=planned_rr,
                    rr=rr_realized,
                    pnl=pnl,
                )

                if sfx == "sl":
                    self.record_stop_loss_event()
                elif sfx == "tp":
                    self.record_take_profit_event()

            _save_state(st)
        except Exception:
            # never raise from outcome polling
            pass

    def record_take_profit_event(self) -> None:
        """Call this when TP is hit (full or major partial).
        This resets the stop-streak (count/cooldown) so new trades are allowed immediately.
        """
        try:
            self.clear_stop_streak()
            _jlog("risk.take_profit.reset_streak")
        except Exception:
            # non-fatal
            pass

    def __init__(self, live: bool, env_path: Optional[str] = None):
        self.live = live
        self.env = load_env(env_path or ".env")
        self.ex: Optional[ExchangeService] = None
        if self.live:
            try:
                testnet = str(self.env.get("TESTNET", "false")).strip().lower() in ("1","true","yes","on","y")
                self.ex = ExchangeService(self.env.get("BYBIT_API_KEY"), self.env.get("BYBIT_API_SECRET"), testnet=testnet)
            except Exception as e:
                print(f"[order_manager] failed to init ExchangeService: {e}")
                self.ex = None
        # Auto-load live equity if enabled
        self._load_live_equity()

        # tick & qty steps may be provided via .env to avoid REST calls
        # e.g. TICK_SIZE_MAP='BTC/USDT:USDT:0.5,ETH/USDT:USDT:0.05'
        self._tick_map = _parse_map(self.env.get("TICK_SIZE_MAP"))
        self._qstep_map = _parse_map(self.env.get("QTY_STEP_MAP"))

        self.account_equity = float(str(self.env.get("ACCOUNT_EQUITY", "10000")).replace(",", "."))
        self.risk_pct = float(str(self.env.get("RISK_PCT", "1")).replace(",", "."))
        self.place_stop_market = _to_bool(self.env.get("PLACE_STOP_MARKET", "true"), True)

    # ---------- helpers ----------
    def tick_size(self, symbol: str) -> float:
        sym = symbol.upper()
        if sym in self._tick_map:
            return self._tick_map[sym]
        # Fallback tick if not configured
        return float(self.env.get("DEFAULT_TICK_SIZE", "0.1"))

    def qty_step(self, symbol: str) -> float:
        sym = symbol.upper()
        if sym in self._qstep_map:
            return self._qstep_map[sym]
        return float(self.env.get("DEFAULT_QTY_STEP", "0.001"))

    def _round_price(self, symbol: str, px: float) -> float:
        t = self.tick_size(symbol)
        if t <= 0:
            return px
        k = round(px / t)
        return max(t, k * t)

    def _round_qty(self, symbol: str, qty: float) -> float:
        q = self.qty_step(symbol)
        if q <= 0:
            return qty
        k = math.floor(qty / q)
        return max(q, k * q)

    # ---------- risk/plan ----------
    def make_plan(
        self,
        symbol: str,
        direction: str,
        level_price: float,
        sweep_extreme: float,
        entry_ticks: int,
        stop_by_sweep: bool,
        stop_ticks: int,
        rr: float,
        tif_minutes: Optional[int] = None,
    ) -> OrderPlan:
        """
        Build the entry/SL/TP plan for a false-break setup at `level_price`.
        direction: 'LONG' (false break of support) or 'SHORT' (false break of resistance)
        sweep_extreme: the low (for LONG) or high (for SHORT) reached during penetration
        """
        direction = direction.upper()
        assert direction in ("LONG", "SHORT")
        tick = self.tick_size(symbol)

        # entry is N ticks beyond the level back into the range
        if direction == "LONG":
            entry = level_price + entry_ticks * tick
            stop = sweep_extreme if stop_by_sweep else (level_price - stop_ticks * tick)
            take = entry + rr * (entry - stop)
            side = "BUY"
        else:
            entry = level_price - entry_ticks * tick
            stop = sweep_extreme if stop_by_sweep else (level_price + stop_ticks * tick)
            take = entry - rr * (stop - entry)
            side = "SELL"

        entry = self._round_price(symbol, entry)
        stop = self._round_price(symbol, stop)
        take = self._round_price(symbol, take)

        # position size: risk % of equity per trade
        risk_money = self.account_equity * (self.risk_pct / 100.0)
        per_unit_risk = max(tick, abs(entry - stop))
        qty_raw = risk_money / per_unit_risk
        qty = self._round_qty(symbol, qty_raw)

        plan = OrderPlan(
            symbol=symbol, side=side, entry=entry, stop=stop, take=take, qty=qty,
            rr=rr, time_in_force="GTC",
            tif_deadline=(datetime.now(timezone.utc) + timedelta(minutes=tif_minutes)) if tif_minutes else None
        )
        return plan

    # ---------- execution ----------

    def place(self, plan: OrderPlan) -> OrderIds:
        """
        Place entry + SL/TP orders according to the plan and persist full trade context.

        Logs a rich "order.place.requested" event into events.jsonl so that the
        analytics/table can reconstruct entry/SL/TP, risk and RR for each signal.
        """
        # idempotency & logging
        tid = f"fb-{_plan_fingerprint(plan)}"
        risk_pct = float(self.risk_pct)
        _jlog(
            "order.place.requested",
            symbol=plan.symbol,
            side=plan.side,
            entry=plan.entry,
            stop=plan.stop,
            take=plan.take,
            qty=plan.qty,
            rr=plan.rr,
            risk_pct=risk_pct,
            trade_id=tid,
        )

        # load state and reuse existing orders if we already have them
        st = _load_state()
        trades = st.setdefault("trades", {})
        existing = trades.get(tid)
        if existing and existing.get("order_ids"):
            ids = existing["order_ids"]
            return OrderIds(ids.get("entry"), ids.get("sl"), ids.get("tp"))

        # common trade snapshot (used for both test and live modes)
        now = datetime.now(timezone.utc)
        tif_deadline = plan.tif_deadline
        if tif_deadline is None:
            # optional extra TIF from SLIPPAGE_CANCEL_MINUTES if not provided in plan
            try:
                slp = int(str(self.env.get("SLIPPAGE_CANCEL_MINUTES", "5")).strip() or "0")
            except Exception:
                slp = 0
            if slp > 0:
                tif_deadline = now + timedelta(minutes=slp)

        base_trade = {
            "symbol": plan.symbol,
            "side": plan.side,
            "entry": float(plan.entry),
            "stop": float(plan.stop),
            "take": float(plan.take),
            "qty": float(plan.qty),
            "rr": float(plan.rr),
            "risk_pct": risk_pct,
            "signal_id": getattr(plan, "signal_id", None),
            "created_at": now.isoformat(),
            "tif_deadline": tif_deadline.isoformat() if tif_deadline else None,
        }

        # --- TEST MODE: no real orders, but we still persist trade context ---
        if not self.live or self.ex is None:
            print(f"[TEST] PLACE {plan.side}_STOP {plan.symbol} qty={plan.qty} entry={plan.entry} SL={plan.stop} TP={plan.take} RR={plan.rr}")
            trades[tid] = {
                **(existing or {}),
                **base_trade,
                "order_ids": {"entry": "TEST-ENTRY", "sl": "TEST-SL", "tp": "TEST-TP"},
            }
            _save_state(st)
            return OrderIds(entry_id="TEST-ENTRY", stop_id="TEST-SL", take_id="TEST-TP")

        # --- LIVE MODE: real orders via ExchangeService ---
        try:
            order_type = "stop_market" if self.place_stop_market else "stop_limit"

            # entry stop
            entry_order = self.ex.create_order(
                plan.symbol,
                order_type,
                plan.side.lower(),
                plan.qty,
                None,
                {
                    "stopPrice": float(plan.entry),
                    "timeInForce": plan.time_in_force,
                    "clientOrderId": tid + "-entry",
                },
            )

            # protective SL
            sl_order = self.ex.create_order(
                plan.symbol,
                "stop_market",
                "sell" if plan.side == "BUY" else "buy",
                plan.qty,
                None,
                {
                    "stopPrice": float(plan.stop),
                    "reduceOnly": True,
                    "clientOrderId": tid + "-sl",
                },
            )

            # take-profit
            tp_order = self.ex.create_order(
                plan.symbol,
                "take_profit_market",
                "sell" if plan.side == "BUY" else "buy",
                plan.qty,
                None,
                {
                    "stopPrice": float(plan.take),
                    "reduceOnly": True,
                    "clientOrderId": tid + "-tp",
                },
            )

            ids = OrderIds(
                entry_id=str(entry_order.get("id")),
                stop_id=str(sl_order.get("id")),
                take_id=str(tp_order.get("id")),
            )

            # persist trade with full context + live order IDs
            trades[tid] = {
                **(existing or {}),
                **base_trade,
                "order_ids": {"entry": ids.entry_id, "sl": ids.stop_id, "tp": ids.take_id},
            }
            _save_state(st)
            return ids

        except Exception as e:
            # if anything goes wrong, log and fail gracefully
            print(f"[LIVE] place orders failed: {e}")
            _jlog("order.place.failed", symbol=plan.symbol, side=plan.side, trade_id=tid, error=str(e))
            return OrderIds(entry_id=None, stop_id=None, take_id=None)


    def cancel_symbol_entries(self, symbol: str) -> int:
        """Cancel all open entry orders for symbol (clientOrderId endswith '-entry').
        Live: scans open orders and cancels entries; Test: marks matching trades as cancelled.
        Returns number of cancelled entries.
        """
        cancelled = 0
        if not self.live or self.ex is None:
            # test-mode: mark in state
            try:
                st = _load_state()
                trades = st.get('trades') or {}
                for tid, tr in list(trades.items()):
                    if tr.get('symbol') != symbol:
                        continue
                    if tr.get('cancelled') or tr.get('closed'):
                        continue
                    tr['cancelled'] = True
                    cancelled += 1
                if cancelled:
                    _save_state(st)
            except Exception:
                pass
            print(f"[TEST] CANCEL all entry orders for {symbol} -> {cancelled}")
            if cancelled:
                _jlog("order.entry.batch_cancel", symbol=symbol, cancelled=cancelled, mode="test")
            return cancelled
        try:
            opens = self.ex.ex.fetch_open_orders(symbol=symbol) or []
            for o in opens:
                coid = str(o.get('clientOrderId') or '')
                oid = str(o.get('id') or '')
                if coid.endswith('-entry') or coid.lower().endswith('entry'):
                    if self.cancel_entry(symbol, oid):
                        cancelled += 1
            if cancelled:
                _jlog("order.entry.batch_cancel", symbol=symbol, cancelled=cancelled, mode="live")
            return cancelled
        except Exception as e:
            print(f"[LIVE] cancel_symbol_entries failed: {e}")
            return cancelled

    def cancel_entry(self, symbol: str, order_id: Optional[str]) -> bool:
        """Cancel a single entry order by ID (with logging)."""
        if not order_id:
            return True
        if not self.live or self.ex is None:
            print(f"[TEST] CANCEL entry {order_id} on {symbol}")
            _jlog("order.entry.cancel", symbol=symbol, order_id=order_id, mode="test")
            return True
        try:
            self.ex.cancel_order(order_id, symbol)
            _jlog("order.entry.cancel", symbol=symbol, order_id=order_id, mode="live")
            return True
        except Exception as e:
            print(f"[LIVE] cancel entry failed: {e}")
            return False


    def cancel_all(self, symbol: str, ids: OrderIds, reason: str = "manual") -> bool:
        """Cancel entry, SL and TP orders for a trade.

        Logs a single aggregated "order.cancel.ok" event with the provided reason.
        """
        ok1 = self.cancel_entry(symbol, ids.entry_id)
        ok2 = self.cancel_entry(symbol, ids.stop_id)
        ok3 = self.cancel_entry(symbol, ids.take_id)
        _jlog(
            "order.cancel.ok",
            symbol=symbol,
            reason=reason,
            ids={"entry": ids.entry_id, "sl": ids.stop_id, "tp": ids.take_id},
        )
        return ok1 and ok2 and ok3

    def _load_live_equity(self) -> None:
        """
        If USE_LIVE_EQUITY=true and live, query Bybit balance via ccxt and set account_equity.
        Falls back to ACCOUNT_EQUITY from .env on any error.
        """
        try:
            use_flag = str(self.env.get("USE_LIVE_EQUITY", "false")).strip().lower() in ("1","true","yes","on","y")
            if not (self.live and use_flag and self.ex is not None):
                return
            cur = str(self.env.get("LIVE_EQUITY_CURRENCY", "USDT")).upper()
            src = str(self.env.get("LIVE_EQUITY_SOURCE", "total")).strip().lower()  # total|free|used
            bal = self.ex.ex.fetch_balance()
            val = None; key_used = None
            for key in (src, "total", "free", "used"):
                d = bal.get(key) or {}
                if isinstance(d, dict):
                    v = d.get(cur) if cur in d else d.get(cur.upper())
                    if v is not None:
                        val = float(v); key_used = key; break
            if val is not None and val > 0:
                self.account_equity = float(val)
                print(f"[order_manager] live equity {key_used}:{cur}={self.account_equity}")
        except Exception as e:
            print(f"[order_manager] live equity fetch failed: {e}")
            # keep self.account_equity from .env



    def is_filled(self, symbol: str, entry_id: str | None) -> bool:
        """Return True if entry order considered FILLED.
        Live: query ccxt to check that entry order is not open and position exists.
        Test: simulate as not filled (or implement your policy).
        """
        if not entry_id:
            return False
        if not self.live or self.ex is None:
            # In test mode we don't auto-fill; return False so logic remains deterministic
            print(f"[TEST] CHECK FILLED entry {entry_id} on {symbol} -> False")
            return False
        try:
            # Bybit: we may not have fetch_order in wrapper; use open-orders scan
            open_orders = self.ex.ex.fetch_open_orders(symbol=symbol)
            if any(str(o.get("id")) == str(entry_id) or str(o.get("clientOrderId")) == str(entry_id) for o in open_orders):
                return False  # still open
            # additionally confirm we have a position
            try:
                poss = self.ex.ex.fetch_positions([symbol])
                for p in poss or []:
                    if symbol in (p.get("symbol") or "") and abs(float(p.get("contracts") or p.get("info",{}).get("size") or 0)) > 0:
                        return True
            except Exception:
                # fallback: if order is not open, we consider filled (could be canceled though)
                return True
            return False
        except Exception as e:
            print(f"[LIVE] is_filled check failed: {e}")
            return False


    def move_stop(self, symbol: str, side: str, new_stop: float, qty: float | None = None) -> bool:
        """Move protective stop to new_stop (reduce-only) and log the adjustment.

        If qty is None, try to infer current position size via ccxt fetch_positions.
        """

        def _record_move(qty_val):
            try:
                st = _load_state()
                trades = st.get("trades") or {}
                updated_tid = None
                for tid, tr in trades.items():
                    if tr.get("symbol") != symbol:
                        continue
                    if tr.get("closed") or tr.get("cancelled"):
                        continue
                    tr_side = str(tr.get("side") or "").upper()
                    if tr_side and tr_side != side.upper():
                        continue
                    # update stored stop level for analytics
                    tr["stop"] = float(new_stop)
                    trades[tid] = tr
                    updated_tid = tid
                    break
                if updated_tid:
                    _save_state(st)
                    _jlog(
                        "order.stop.moved",
                        symbol=symbol,
                        side=side,
                        new_stop=new_stop,
                        qty=qty_val,
                        trade_id=updated_tid,
                    )
                else:
                    _jlog(
                        "order.stop.moved",
                        symbol=symbol,
                        side=side,
                        new_stop=new_stop,
                        qty=qty_val,
                    )
            except Exception:
                # logging/state update issues must not break trading loop
                pass

        if not self.live or self.ex is None:
            print(f"[TEST] MOVE SL {symbol} -> {new_stop}")
            _record_move(qty)
            return True

        try:
            if qty is None:
                try:
                    poss = self.ex.ex.fetch_positions([symbol])
                    for p in poss or []:
                        if symbol in (p.get("symbol") or ""):
                            contracts = float(
                                p.get("contracts") or p.get("info", {}).get("size") or 0
                            )
                            qty = abs(contracts)
                            break
                except Exception:
                    pass
            if not qty or qty <= 0:
                print("[LIVE] move_stop: qty unknown -> abort")
                return False

            opposite = "sell" if side.upper() == "BUY" else "buy"
            params = {"stopPrice": float(new_stop), "reduceOnly": True}
            o = self.ex.create_order(symbol, "stop_market", opposite, qty, None, params)
            print(f"[LIVE] move_stop placed: {o}")
            _record_move(qty)
            return True
        except Exception as e:
            print(f"[LIVE] move_stop failed: {e}")
            return False


    # ---- instance wrappers for risk/capacity helpers ----
    def is_stop_streak_blocked(self) -> bool:
        """Return True if stop-streak cooldown or limit reached (instance method)."""
        return is_stop_streak_blocked(self)

    def open_trades_count(self) -> int:
        """Estimate number of open trades (instance method wrapper)."""
        return open_trades_count(self)

    def cancel_expired_entries(self) -> int:
        """Cancel entry orders whose TIF deadline has passed (instance method wrapper)."""
        return cancel_expired_entries(self)




# ---------- risk gate helpers ----------
def is_stop_streak_blocked(self) -> bool:
    """Return True if stop-streak cooldown or limit reached."""
    try:
        limit = int(str(self.env.get("STOP_SERIES_LIMIT", 3)))
    except Exception:
        limit = 3
    info = self._stop_streak_info()
    # cooldown_until optional ISO
    cu = info.get("cooldown_until")
    if cu:
        try:
            from datetime import datetime, timezone
            if datetime.now(timezone.utc) <= datetime.fromisoformat(cu):
                return True
        except Exception:
            pass
    try:
        return int(info.get("count", 0)) >= limit
    except Exception:
        return False

def open_trades_count(self) -> int:
    """Estimate number of open trades.
    Live: count non-zero positions (and open entry orders as bonus).
    Test: count records in state.trades that are not closed/cancelled.
    """
    st = _load_state()
    cnt = 0
    # test-mode heuristic
    for tid, tr in (st.get("trades") or {}).items():
        if tr.get("closed") or tr.get("cancelled"):
            continue
        if tr.get("order_ids", {}).get("entry"):
            cnt += 1
    # live enhancement
    if self.live and self.ex is not None:
        try:
            poss = self.ex.ex.fetch_positions([])
            for p in poss or []:
                contracts = float(p.get("contracts") or p.get("info",{}).get("size") or 0)
                if abs(contracts) > 0:
                    cnt += 1
        except Exception:
            pass
    return cnt

# ---------- TIF auto-cancel ----------
def cancel_expired_entries(self) -> int:
    """Cancel entry orders whose TIF deadline has passed and are still not filled.
    Returns number of cancelled entries.
    """
    st = _load_state()
    trades = st.get("trades") or {}
    now = datetime.now(timezone.utc)
    cancelled = 0
    for tid, tr in list(trades.items()):
        if tr.get("cancelled") or tr.get("closed"):
            continue
        dl = tr.get("tif_deadline")
        symbol = tr.get("symbol")
        entry_id = ((tr.get("order_ids") or {}).get("entry"))
        if not dl or not symbol or not entry_id:
            continue
        try:
            ddl = datetime.fromisoformat(dl)
        except Exception:
            continue
        if now <= ddl:
            continue
        # deadline passed -> check filled
        if self.is_filled(symbol, entry_id):
            continue
        if self.cancel_entry(symbol, entry_id):
            trades[tid]["cancelled"] = True
            _jlog("order.tif_cancelled", trade_id=tid, symbol=symbol, entry_id=entry_id, reason="deadline")
            cancelled += 1
    if cancelled:
        _save_state(st)
    return cancelled
