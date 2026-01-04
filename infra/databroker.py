# infra/databroker.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import time
from typing import Dict, Tuple, Optional, List

import pandas as pd

from infra.env import load_env, as_int


def _pick_ts_column(df: pd.DataFrame) -> Optional[str]:
    for c in ("ts", "timestamp", "time", "datetime", "date"):
        if c in df.columns:
            return c
    return None

def _ensure_ts(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["ts","open","high","low","close","volume"])
    d = df.copy()
    tsc = _pick_ts_column(d)
    if tsc != "ts":
        if tsc is None:
            d["ts"] = pd.Timestamp.utcnow()
        else:
            d["ts"] = d[tsc]
    return d.reset_index(drop=True)

def _to_utc_series(col) -> pd.Series:
    s = pd.Series(col)
    if pd.api.types.is_numeric_dtype(s):
        vmax = pd.to_numeric(s, errors="coerce").max()
        unit = "ms" if pd.notna(vmax) and float(vmax) > 1e11 else "s"
        return pd.to_datetime(s, unit=unit, utc=True, errors="coerce")
    return pd.to_datetime(s, utc=True, errors="coerce")

def _utc_now():  # utc-aware now
    return datetime.now(timezone.utc)

def _sec_to_next_midnight_utc(now: Optional[datetime]=None) -> int:
    now = now or _utc_now()
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((nxt - now).total_seconds())

def _sec_to_next_4h_boundary(now: Optional[datetime]=None) -> int:
    now = now or _utc_now()
    hour_block = (now.hour // 4) * 4
    nxt = now.replace(hour=hour_block, minute=0, second=0, microsecond=0) + timedelta(hours=4)
    return int((nxt - now).total_seconds())

@dataclass
class BrokerCfg:
    refresh_1d_sec: int
    refresh_4h_sec: int
    refresh_1h_sec: int
    refresh_1m_sec: int
    refresh_sweep_sec: int
    api_backoff_sec: int
    warmup_sweep_bars: int
    warmup_h1_bars: int

class DataBroker:
    """
    Обёртка над ExchangeService с кэшированием и минимальными запросами.
    Держит буферы OHLCV по символам/TF, обновляет только то, что нужно.
    """
    def __init__(self, exchange_service, env_path: str = ".env"):
        self.ex = exchange_service
        env = load_env(env_path)
        i = lambda k,d: as_int(env.get(k), d)
        self.cfg = BrokerCfg(
            refresh_1d_sec = i("REFRESH_1D_SEC", 86400),
            refresh_4h_sec = i("REFRESH_4H_SEC", 14400),
            refresh_1h_sec = i("REFRESH_1H_SEC", 3600),
            refresh_1m_sec = i("REFRESH_1M_SEC", 60),
            refresh_sweep_sec = i("REFRESH_SWEEP_SEC", 20),
            api_backoff_sec = i("API_BACKOFF_SEC", 2),
            warmup_sweep_bars = i("WARMUP_SWEEP_BARS", 200),
            warmup_h1_bars = i("WARMUP_H1_BARS", 60),
        )
        # caches
        self._tick_cache: Dict[str, Optional[float]] = {}
        # key: (symbol, tf) -> (DataFrame, last_fetch_ts, ttl_sec)
        self._buf: Dict[Tuple[str,str], Tuple[pd.DataFrame, float, int]] = {}

    # --------- low-level fetch with backoff ----------
    def _fetch(self, symbol: str, tf: str, limit: int) -> pd.DataFrame:
        """Fetch with retries/backoff via exchange retry helper."""
        def call():
            return self.ex.fetch_ohlcv(symbol, tf, limit=limit)
        try:
            df = self.ex._retry(call)
            return _ensure_ts(df)
        except Exception as e:
            raise


    # --------- tick size ----------
    def get_tick(self, symbol: str) -> Optional[float]:
        if symbol in self._tick_cache:
            return self._tick_cache[symbol]
        try:
            m = self.ex.ex.market(symbol) if hasattr(self.ex, "ex") else None
            if not m:
                self._tick_cache[symbol] = None
                return None
            limits = m.get("limits") or {}
            price = limits.get("price") or {}
            ts = price.get("min")
            if ts:
                self._tick_cache[symbol] = float(ts)
                return self._tick_cache[symbol]
            prec = (m.get("precision") or {}).get("price")
            if prec is not None:
                self._tick_cache[symbol] = float(10 ** (-int(prec)))
                return self._tick_cache[symbol]
        except Exception:
            pass
        self._tick_cache[symbol] = None
        return None

    # --------- public API ----------
    def warmup(self, symbols: List[str], sweep_tf: str, need_h4: bool):
        """
        Разовый прогрев при старте /run:
        - D1 (≈250 баров)
        - H4 (≈250 баров) — если нужен
        - sweep_tf (M5) — последние WARMUP_SWEEP_BARS
        """
        for s in symbols:
            # D1 — TTL до полуночи
            ttl1d = max(self.cfg.refresh_1d_sec, _sec_to_next_midnight_utc())
            self._buf[(s, "1d")] = (self._fetch(s, "1d", limit=250), time.time(), ttl1d)
            if need_h4:
                ttl4h = max(self.cfg.refresh_4h_sec, _sec_to_next_4h_boundary())
                self._buf[(s, "4h")] = (self._fetch(s, "4h", limit=250), time.time(), ttl4h)
            # sweep tf — тёплый старт (храним буфер, дальше обновляем 1–2 барами)
            self._buf[(s, sweep_tf)] = (self._fetch(s, sweep_tf, limit=self.cfg.warmup_sweep_bars),
                                        time.time(), self.cfg.refresh_sweep_sec)

    def get_ohlcv(self, symbol: str, tf: str, need: int | None = None, warm: bool = False) -> pd.DataFrame:
        """
        Возвращает локальный буфер по (symbol, tf).
        При warm=True делает тёплый старт (не чаще чем TTL), дальше обновляет маленькими порциями.
        """
        now = time.time()
        key = (symbol, tf)
        # подобрать TTL по TF
        if tf == "1d":
            ttl = max(self.cfg.refresh_1d_sec, _sec_to_next_midnight_utc())
        elif tf == "4h":
            ttl = max(self.cfg.refresh_4h_sec, _sec_to_next_4h_boundary())
        elif tf == "1h":
            ttl = self.cfg.refresh_1h_sec
        elif tf == "1m":
            ttl = self.cfg.refresh_1m_sec
        else:
            ttl = self.cfg.refresh_sweep_sec

        # если буфера нет — тёплый старт
        if key not in self._buf:
            if warm:
                base = self.cfg.warmup_sweep_bars if tf.endswith("m") else (self.cfg.warmup_h1_bars if tf=="1h" else 250)
                df = self._fetch(symbol, tf, limit=max(base, need or 1))
            else:
                df = self._fetch(symbol, tf, limit=max(need or 1, 2))
            self._buf[key] = (df, now, ttl)
            return df

        df, ts, old_ttl = self._buf[key]
        # если TTL не вышел — достаточно текущего буфера (и он покрывает need)
        if (now - ts) < old_ttl:
            if need is None or len(df) >= need:
                return df

        # нужно обновить
        if tf.endswith("m"):
            # для минутного TF — подтянуть 1-2 последних бара
            latest = self._fetch(symbol, tf, limit=max(need or 2, 2))
            merged = pd.concat([df, latest], ignore_index=True)
            # нормализуем временную метку
            if "ts" not in merged.columns:
                merged = _ensure_ts(merged)
            merged["ts_dt"] = _to_utc_series(merged["ts"])
            merged = merged.drop_duplicates(subset=["ts_dt"], keep="last").sort_values("ts_dt").drop(columns=["ts_dt"])
            # ограничим буфер
            keep = max(self.cfg.warmup_sweep_bars, need or 0, 200)
            merged = merged.tail(keep).reset_index(drop=True)
            self._buf[key] = (merged, now, ttl)
            return merged
        else:
            # для H1/H4/D1 — тянем столько, сколько нужно (редко)
            base = self.cfg.warmup_h1_bars if tf == "1h" else 250
            latest = self._fetch(symbol, tf, limit=max(base, need or 1))
            self._buf[key] = (latest, now, ttl)
            return latest