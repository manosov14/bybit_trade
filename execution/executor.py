from __future__ import annotations

from dataclasses import dataclass

from infra.specs import SpecsResolver, round_down
from strategies.base import SignalIntent


@dataclass
class ExecutionConfig:
    """Safety gates and defaults for order placement."""

    allow_live: bool = False  # prevents accidental live trading
    default_order_type: str = "limit"  # "limit" or "market" (strategy may override)


class OrderExecutor:
    """Converts SignalIntent into exchange orders.

    For the first iteration we keep this minimal and safe:
      - by default it does NOT place live orders
      - it only prints what would be done

    When you are ready, we'll extend it with:
      - clientOrderId convention
      - entry + protective SL + TP (reduceOnly)
      - TIF/TTL handling
      - reconcile on restart
    """

    def __init__(self, exchange, env: dict, cfg: ExecutionConfig | None = None):
        self.exchange = exchange
        self.env = env
        self.cfg = cfg or ExecutionConfig()

        self.specs = SpecsResolver(
            tick_map_raw=env.get("TICK_SIZE_MAP"),
            qty_map_raw=env.get("QTY_STEP_MAP"),
            default_tick=float(env.get("DEFAULT_TICK_SIZE", 0.01) or 0.01),
            default_step=float(env.get("DEFAULT_QTY_STEP", 0.001) or 0.001),
        )

    def normalize_intent(self, intent: SignalIntent) -> SignalIntent:
        """Apply tick/step rounding to numeric fields."""
        sym = intent.symbol
        tick = self.specs.tick_size(sym)
        step = self.specs.qty_step(sym)
        # Entry/SL/TP are prices => tick; size is not here yet
        def rp(x):
            return None if x is None else round_down(float(x), tick)

        return SignalIntent(
            symbol=intent.symbol,
            side=intent.side,
            entry=rp(intent.entry),
            sl=rp(intent.sl),
            tp=rp(intent.tp),
            ttl_sec=intent.ttl_sec,
            tag=intent.tag,
            reason=intent.reason,
            meta={**(intent.meta or {}), "tick": tick, "step": step},
        )

    def place_intent(self, intent: SignalIntent) -> dict:
        """Place (or simulate) orders for intent.

        Returns a dict suitable for logging.
        """
        norm = self.normalize_intent(intent)
        if not self.cfg.allow_live:
            return {
                "mode": "dry_run",
                "intent": norm,
            }

        # Live trading path (disabled by default)
        # We'll implement this after your new entry logic is ready.
        raise NotImplementedError("Live execution is disabled. Set allow_live=True and implement order placement.")
