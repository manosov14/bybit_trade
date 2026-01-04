from __future__ import annotations
from typing import Optional
from usecases.order_manager import _jlog

def process_fills(scanner, symbol: Optional[str]=None) -> None:
    """
    Housekeeping hook:
      1) cancel expired entries by TIF,
      2) poll SL/TP outcomes to update stop-streak,
      3) if entry filled for an ARMED setup and есть экстремум прокола — двигаем SL за экстремум.
    Все вызовы безопасны (не роняют цикл).
    """
    # 1) & 2) risk/order housekeeping
    try:
        # Auto-cancel outdated entries
        if hasattr(scanner, 'om'):
            try:
                scanner.om.cancel_expired_entries()
            except Exception:
                pass
            try:
                scanner.om.poll_and_book_outcomes()
            except Exception:
                pass
    except Exception:
        pass

    # 3) Move SL behind sweep extreme for filled entries (optional states map)
    try:
        states = getattr(scanner, 'states', None)
        if not isinstance(states, dict):
            return
        items = list(states.items())
        for key, st in items:
            try:
                sym, _lvl = key if isinstance(key, (list, tuple)) and len(key) == 2 else (None, None)
                if symbol and sym and sym != symbol:
                    continue
                ids = getattr(st, 'order_ids', None) or getattr(st, 'ids', None) or {}
                entry_id = getattr(ids, 'entry_id', None) if hasattr(ids, 'entry_id') else ids.get('entry')
                if not entry_id or not sym:
                    continue
                filled = False
                try:
                    filled = bool(scanner.om.is_filled(sym, entry_id)) if hasattr(scanner, 'om') else False
                except Exception:
                    filled = False
                if not filled:
                    continue
                # log trade entry opening once (best-effort)
                try:
                    already = getattr(st, "_entry_logged", False)
                except Exception:
                    already = False
                if not already:
                    try:
                        side = getattr(st, "direction", None) or getattr(st, "side", None)
                        entry = getattr(st, "entry", None) or getattr(st, "entry_price", None)
                        stop = getattr(st, "stop", None) or getattr(st, "stop_loss", None)
                        take = getattr(st, "take", None) or getattr(st, "take_profit", None)
                        level_id = getattr(st, "level_id", None) or getattr(st, "id", None)
                        qty = getattr(st, "qty", None) or getattr(st, "size", None)
                        risk_pct = getattr(st, "risk_pct", None) or getattr(st, "risk", None)
                        trade_id = getattr(st, "trade_id", None)
                        _jlog(
                            "trade.entry.opened",
                            symbol=sym,
                            side=side,
                            entry=entry,
                            stop=stop,
                            take=take,
                            qty=qty,
                            risk_pct=risk_pct,
                            level_id=level_id,
                            trade_key=str(key),
                            trade_id=trade_id,
                        )
                    except Exception:
                        pass
                    # mark level as fully used on first actual entry fill
                    try:
                        if level_id and hasattr(scanner, "state"):
                            scanner.state.mark_used(sym, level_id)
                    except Exception:
                        # logging/state issues must not break trading loop
                        pass
                    try:
                        setattr(st, "_entry_logged", True)
                    except Exception:
                        pass
# move SL behind extreme
                sx = getattr(st, 'sweep_extreme', None) or getattr(st, 'extreme', None)
                if sx is None:
                    continue
                side = getattr(st, 'direction', None) or getattr(st, 'side', None) or 'BUY'
                try:
                    scanner.om.move_stop(sym, side, float(sx))
                except Exception:
                    pass
            except Exception:
                continue
    except Exception:
        pass
