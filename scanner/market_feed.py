from __future__ import annotations
from dataclasses import dataclass
from typing import Dict
import pandas as pd

from infra.env import load_env, as_bool
from infra.exchange_ccxt import ExchangeService
from infra.databroker import DataBroker

@dataclass
class MarketFeed:
    env_path: str = ".env"
    ex: ExchangeService | None = None
    broker: DataBroker | None = None

    def __post_init__(self):
        env = load_env(self.env_path)
        if self.ex is None:
            testnet = as_bool(env.get("TESTNET", "false"), False)
            self.ex = ExchangeService(env.get("BYBIT_API_KEY"), env.get("BYBIT_API_SECRET"), testnet=testnet)
        if self.broker is None:
            self.broker = DataBroker(self.ex, env_path=self.env_path)

    def candles(self, symbol: str, tf: str, need: int) -> pd.DataFrame:
        return self.broker.get_ohlcv(symbol, tf, need=need)

    def snapshot(self, symbol: str) -> Dict[str, pd.DataFrame]:
        return {
            "1d": self.candles(symbol, "1d", need=260),
            "4h": self.candles(symbol, "4h", need=400),
            "1h": self.candles(symbol, "1h", need=200),
            "5m": self.candles(symbol, "5m", need=200),
            "1m": self.candles(symbol, "1m", need=120),
        }
