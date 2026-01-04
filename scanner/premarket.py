from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

import pandas as pd

from infra.env import load_env
from .market_feed import MarketFeed
from .level_watcher import LevelWatcher
from .sweep_detector import SweepDetector
from .filter_adapter import FilterPipelineAdapter
from usecases.order_manager import OrderManager
from .audit_logger import AuditLogger
from .state_store import SignalStateStore

UTC = timezone.utc

def _msk_day_end_iso(ts:int|None=None)->str:
    from datetime import datetime, timedelta, timezone
    MSK = timezone(timedelta(hours=3))
    now = datetime.now(MSK) if ts is None else datetime.fromtimestamp(int(ts if ts<1e12 else ts/1000), tz=MSK)
    end = now.replace(hour=23,minute=59,second=59,microsecond=0)
    return end.astimezone(timezone.utc).isoformat()

def _midnight_utc(dt: Optional[datetime]=None) -> datetime:
    dt = dt or datetime.now(UTC)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)

def _iso(ts: int) -> str:
    # ts may be seconds or ms
    if ts > 1_000_000_000_000:  # ms
        ts = ts // 1000
    return datetime.fromtimestamp(int(ts), tz=UTC).isoformat(timespec="seconds")

@dataclass
class PremarketScan:
    env_path: str = ".env"

    def __post_init__(self):
        env = load_env(self.env_path)
        self.symbols = [s.strip() for s in str(env.get('SYMBOLS','BTC/USDT:USDT')).split(',') if s.strip()]
        self.days = int(str(env.get('DAYS',10)))
        self.include_inside = str(env.get('INCLUDE_INSIDE','true')).lower() in ('1','true','yes','on','y')
        self.feed = MarketFeed(self.env_path)
        self.levels = LevelWatcher(self.env_path)
        self.detector = SweepDetector()
        self.filters = FilterPipelineAdapter(self.env_path)
        self.om = OrderManager(live=False, env_path=self.env_path)
        self.log = AuditLogger(to_stdout=False)  # будем писать прямо в jsonl
        self.state = SignalStateStore(env_path=self.env_path)

    def _write_event(self, ts_iso: str, etype: str, **payload):
        # записываем напрямую, не полагаясь на AuditLogger.event (который проставляет текущий ts)
        rec = {"ts": ts_iso, "type": etype, **payload}
        os.makedirs(self.log.log_dir, exist_ok=True)
        with open(self.log.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def scan_today(self, symbols: Optional[List[str]]=None):
        symbols = symbols or self.symbols
        start = _midnight_utc()
        end_ts = int(datetime.now(UTC).timestamp())

        for sym in symbols:
            # подгружаем достаточно истории; фильтруем до конца текущего момента
            d1 = self.feed.candles(sym, "1d", need=260)
            h4 = self.feed.candles(sym, "4h", need=400)
            h1 = self.feed.candles(sym, "1h", need=600)
            m1 = self.feed.candles(sym, "1m", need=1500)

            # нормализуем по времени
            def _flt(df: pd.DataFrame, ts_to: int) -> pd.DataFrame:
                if "ts" not in df.columns:
                    return df
                # Make types comparable: df['ts'] is tz-aware pandas datetime; ts_to is unix seconds
                try:
                    import pandas as pd
                    cutoff = pd.to_datetime(int(ts_to), unit='s', utc=True)
                    return df[df["ts"] <= cutoff]
                except Exception:
                    return df

            # итерируем по минутным барам c 00:00 UTC
            if "ts" not in m1.columns:
                continue
            for ts in m1["ts"].astype(int).tolist():
                if ts < int(start.timestamp()) or ts > end_ts:
                    continue
                d1_up = _flt(d1, ts)
                h4_up = _flt(h4, ts)
                h1_up = _flt(h1, ts)
                m1_up = _flt(m1, ts)

                try:
                    lvls = self.levels.get_levels_d1(sym, d1_up, self.days, self.include_inside)
                except Exception as e:
                    self._write_event(_iso(ts), "symbol_error", symbol=sym, error=f"levels:{e}")
                    continue

                for L in lvls:
                    try:
                        ev = self.detector.detect(sym, L, m1_up, h1_up)
                    except Exception as e:
                        self._write_event(_iso(ts), "symbol_error", symbol=sym, error=f"detect:{e}")
                        continue
                    if ev is None:
                        continue

                    # прогон через фильтры (в т.ч. скорость/сессии на текущих h1)
                    try:
                        fres = self.filters.run(ev, L, d1_up, h4_up, h1_up, ignore_speed=False)
                    except Exception as e:
                        self._write_event(_iso(ts), "symbol_error", symbol=sym, error=f"filters:{e}")
                        continue

                    if not getattr(fres, "accepted", True):
                        self._write_event(_iso(getattr(ev,'m1_bar_ts', ts)), "filtered_out",
                                          symbol=sym, level_id=L.id, reasons=",".join(getattr(fres,"reasons",[])),
                                          trend=getattr(fres,"extras",{}).get("trend",{}),
                                          sessions=getattr(fres,"extras",{}).get("sessions",[]),
                                          speed=getattr(fres,"extras",{}).get("speed",{}),
                                          depth_atr=getattr(ev,"depth_atr_h1",None))
                        try:
                            self.state.set_cooldown_until(sym, L.id, _msk_day_end_iso(ts))
                        except Exception:
                            pass
                        continue

                    # Ставим кулдаун на оставшуюся часть дня, чтобы не рассматривать повторно
                    try:
                        self.state.set_cooldown_until(sym, L.id, _msk_day_end_iso(ts))
                    except Exception:
                        pass

                    # план без размещения ордеров
                    try:
                        entry, stop, take, side = self.om.compute_prices(L.price, ev.extreme_price, ev.side.lower())
                        qty = self.om.compute_qty(sym, stop, entry)
                    except Exception as e:
                        self._write_event(_iso(getattr(ev,'m1_bar_ts', ts)), "symbol_error",
                                          symbol=sym, error=f"plan:{e}")
                        continue

                    self._write_event(_iso(getattr(ev,'m1_bar_ts', ts)), "plan_ready", trend=getattr(fres,"extras",{}).get("trend",{}), sessions=getattr(fres,"extras",{}).get("sessions",[]), speed=getattr(fres,"extras",{}).get("speed",{}),
                                      symbol=sym, level_id=L.id, side=side, entry=entry, stop=stop, take=take, qty=qty, attempt=1)
                    self._write_event(_iso(getattr(ev,'m1_bar_ts', ts)), "plan_extras",
                                      symbol=sym, level_id=L.id,
                                      trend=getattr(fres,"extras",{}).get("trend",{}),
                                      sessions=getattr(fres,"extras",{}).get("sessions",[]),
                                      speed=getattr(fres,"extras",{}).get("speed",{}),
                                      depth_atr=getattr(ev,"depth_atr_h1",None), side=ev.side,
                                      entry=entry, stop=stop, take=take)
