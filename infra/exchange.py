from __future__ import annotations

import os
import time
from typing import Any, Callable

import pandas as pd


class ExchangeService:
    """Thin, reusable wrapper around ccxt.bybit.

    Design goals:
      - single place to configure Bybit linear USDT perpetuals
      - safe retries with backoff
      - optional testnet (sandbox) support

    Notes:
      - network calls are not unit-tested in this repo; validate on testnet.
    """

    _markets: dict | None = None
    _last_specs_reload: float = 0.0

    def __init__(self, api_key: str | None = None, api_secret: str | None = None, testnet: bool = False):
        # Lazy import: allow running non-trading commands without ccxt installed.
        try:
            import ccxt  # type: ignore
        except Exception as e:
            raise ModuleNotFoundError(
                "ccxt is not installed. Install dependencies: pip install -r requirements.txt"
            ) from e

        self._ccxt = ccxt

        # Base exchange
        self.ex = ccxt.bybit(
            {
                "apiKey": api_key or "",
                "secret": api_secret or "",
                "enableRateLimit": True,
                "timeout": int(os.environ.get("CCXT_TIMEOUT_MS", "20000")),
            }
        )

        # Force linear USDT perpetuals (swap)
        self.ex.options = self.ex.options or {}
        self.ex.options.update(
            {
                "defaultType": "swap",
                "defaultSubType": "linear",
                "defaultSettle": "USDT",
                # both variants are used across ccxt/bybit versions
                "recv_window": 25000,
                "recvWindow": 25000,
                "adjustForTimeDifference": True,
            }
        )

        # Sandbox (testnet)
        if testnet:
            try:
                self.ex.set_sandbox_mode(True)
            except Exception:
                # Older ccxt versions may not support sandbox for bybit via this method.
                pass

        # Optional time sync (can stall on some networks)
        sync_time = str(os.environ.get("CCXT_SYNC_TIME", "false")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
            "y",
        )
        if sync_time:
            try:
                self.ex.load_time_difference()
            except Exception:
                pass

    # --------------------------- market specs ---------------------------------
    def reload_specs(self, force: bool = False) -> None:
        """Hot-reload market specs (precision/filters).

        We keep a small cooldown to avoid hammering load_markets().
        """
        now = time.time()
        if (not force) and (now - float(getattr(self, "_last_specs_reload", 0.0)) < 5.0):
            return
        try:
            self._markets = self.ex.load_markets(reload=True)
            self._last_specs_reload = now
        except Exception:
            pass

    def get_symbol_specs(self, symbol: str) -> dict:
        try:
            mk = self._markets or self.ex.load_markets()
            return mk.get(symbol) or {}
        except Exception:
            return {}

    # --------------------------- retry wrapper --------------------------------
    def _retry(self, fn: Callable[..., Any], *args, **kwargs):
        """Retry wrapper with exponential backoff + jitter.

        - Network/429/DDoS errors: exponential sleep, capped.
        - Precision/LOT errors: hot-reload specs once and retry.
        """
        import random

        ccxt = getattr(self, "_ccxt", None)

        max_retries = int(kwargs.pop("_max_retries", 6) or 6)
        base = float(kwargs.pop("_base_delay", 0.5) or 0.5)
        factor = float(kwargs.pop("_factor", 2.0) or 2.0)
        max_sleep = float(kwargs.pop("_max_sleep", 15.0) or 15.0)

        reloaded_specs = False
        last = None
        for attempt in range(max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                last = e
                msg = str(e)

                is_rate = any(s in msg for s in ("429", "rate limit", "Too many visits", "DDoS", "DDoSProtection"))
                is_network = bool(ccxt) and isinstance(
                    e,
                    (
                        getattr(ccxt, "NetworkError", Exception),
                        getattr(ccxt, "DDoSProtection", Exception),
                        getattr(ccxt, "RateLimitExceeded", Exception),
                        getattr(ccxt, "ExchangeNotAvailable", Exception),
                        getattr(ccxt, "RequestTimeout", Exception),
                    ),
                )

                # One-time hot reload on constraint errors
                if (
                    any(k in msg for k in ("InvalidPrice", "MinNotional", "price filter", "LOT_SIZE", "PRECISION"))
                    and not reloaded_specs
                ):
                    try:
                        self.reload_specs(force=True)
                        reloaded_specs = True
                        continue
                    except Exception:
                        pass

                if attempt >= max_retries:
                    break

                sleep_for = min(max_sleep, base * (factor**attempt))
                sleep_for += random.uniform(0, 0.4 * sleep_for)
                if is_rate or is_network:
                    sleep_for = max(sleep_for, 1.0)
                    if is_rate:
                        sleep_for = max(sleep_for, 5.0)
                time.sleep(sleep_for)

        raise last if last else Exception("Unknown exchange error")

    # --------------------------- public data ----------------------------------
    def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 1500) -> pd.DataFrame:
        params = {"category": "linear"}
        data = self._retry(self.ex.fetch_ohlcv, symbol, timeframe=timeframe, limit=limit, params=params)
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df

    def fetch_trades(self, symbol: str, since_ms: int | None = None, limit: int = 1000):
        return self._retry(self.ex.fetch_trades, symbol, since=since_ms, limit=limit)

    def market_conditions_ok(self, symbol: str, min_24h_usdt_vol: float, max_spread_pct: float):
        t = self._retry(self.ex.fetch_ticker, symbol)
        bid, ask = t.get("bid"), t.get("ask")
        spread_ok = bool(bid and ask and (ask - bid) / ((ask + bid) / 2) <= max_spread_pct / 100.0)
        qvol = t.get("quoteVolume") or 0
        vol_ok = qvol >= min_24h_usdt_vol
        return spread_ok and vol_ok, {"spread": (ask - bid) if (bid and ask) else None, "quoteVolume": qvol}

    # --------------------------- trading --------------------------------------
    def create_order(self, symbol: str, typ: str, side: str, amount: float, price: float | None, params: dict | None = None):
        params = params or {}
        return self._retry(self.ex.create_order, symbol, typ, side, amount, price, params)

    def cancel_order(self, order_id: str, symbol: str, params: dict | None = None):
        params = params or {}
        try:
            return self._retry(self.ex.cancel_order, order_id, symbol, params)
        except Exception:
            # fallback: mass cancel
            return self._retry(self.ex.cancel_all_orders, symbol, params)
