from __future__ import annotations

import importlib
import time
from datetime import datetime, timezone

from infra.env import as_bool, as_int, as_list, load_env, normalize_env
from infra.exchange_ccxt import ExchangeService
from infra.databroker import DataBroker
from scanner.audit_logger import AuditLogger
from scanner.market_feed import MarketFeed
from strategies.base import MarketSnapshot, StrategyContext


def _load_strategy(module_path: str):
    """Load a strategy module providing `Strategy`.
    Example: strategies.empty
    """
    mod = importlib.import_module(module_path)
    if not hasattr(mod, "Strategy"):
        raise RuntimeError(f"Strategy module '{module_path}' must define class Strategy")
    return getattr(mod, "Strategy")()


def monitor_loop(env_path: str = ".env") -> None:
    """New runtime loop.

    This intentionally does NOT include the old analysis/entry logic.
    It only:
      - pulls market data snapshots
      - calls a pluggable strategy
      - logs intents into events.jsonl

    Next step: connect intents to execution layer.
    """
    env = normalize_env(load_env(env_path))
    testnet = as_bool(env.get("TESTNET", "false"), False)
    symbols = as_list(env.get("SYMBOLS", ""))
    if not symbols:
        raise SystemExit("SYMBOLS is empty")

    strategy_mod = str(env.get("STRATEGY_MODULE", "strategies.empty") or "strategies.empty").strip()
    strategy = _load_strategy(strategy_mod)

    # Single exchange/broker instance for consistent environment
    ex = ExchangeService(env.get("BYBIT_API_KEY"), env.get("BYBIT_API_SECRET"), testnet=testnet)
    broker = DataBroker(ex, env_path=env_path)
    feed = MarketFeed(env_path=env_path, ex=ex, broker=broker)
    audit = AuditLogger(log_dir=str(env.get("LOG_DIR") or "logs"))

    interval = max(1, as_int(env.get("SCAN_INTERVAL_SEC", "10"), 10))
    print(f"[runtime] started | strategy={strategy_mod} | symbols={len(symbols)} | interval={interval}s | testnet={testnet}")

    while True:
        now = datetime.now(timezone.utc)
        ctx = StrategyContext(now_utc_iso=now.isoformat(), env=env)

        for sym in symbols:
            try:
                tf = feed.snapshot(sym)
                snap = MarketSnapshot(symbol=sym, tf=tf)
                intents = strategy.on_snapshot(snap, ctx) or []

                audit.log_event("snapshot", symbol=sym, frames=list(tf.keys()), intents=len(intents))
                for it in intents:
                    audit.log_event(
                        "intent",
                        symbol=it.symbol,
                        side=it.side,
                        entry=it.entry,
                        sl=it.sl,
                        tp=it.tp,
                        ttl_sec=it.ttl_sec,
                        tag=it.tag,
                        reason=it.reason,
                        meta=it.meta,
                    )
                    print(f"[intent] {it.symbol} {it.side} entry={it.entry} sl={it.sl} tp={it.tp} tag={it.tag}")
            except Exception as e:
                audit.log_event("runtime.error", symbol=sym, error=str(e))
                print(f"[runtime] error {sym}: {e}")

        time.sleep(interval)
