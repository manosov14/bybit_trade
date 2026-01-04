from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any
from datetime import datetime, timezone

from infra.env import load_env, parse_add_levels
from domain.levels import working_levels_d1, LevelsParams
from usecases.scanner_levels_reader import get_pd_levels, decide_for_retro
from domain.trend import d1_trend
from .contracts import Level

def _day_key() -> str:
    # UTC календарный день — для однократного пересчёта уровней в сутки
    return datetime.now(timezone.utc).date().isoformat()

@dataclass
class LevelWatcher:
    env_path: str = ".env"

    def __post_init__(self):
        # Кэшируем уровни на текущий день по символу
        self._cache: Dict[str, List[Level]] = {}
        self._cache_day: str = _day_key()

    def _ensure_day(self):
        # Сбрасываем кэш при смене календарного дня (UTC)
        cur = _day_key()
        if cur != self._cache_day:
            self._cache.clear()
            self._cache_day = cur

    def get_levels_d1(self, symbol: str, d1_df, days: int, include_inside: bool) -> List[Level]:
        """
        Рассчитать уровни D1 по ТЗ:
        - пересчитывать 1 раз в новый день (UTC);
        - ручные уровни брать из .env и включать всегда;
        - H4/MANUAL не фильтруются по тренду (фильтры сделают bypass).
        """
        self._ensure_day()
        key = f"{symbol}|{self._cache_day}"
        if key in self._cache:
            return self._cache[key]

        env = load_env(self.env_path)
        # Тренд D1 нужен для отбора рабочих уровней дня
        d1_sma = int(str(env.get("D1_SMA_LEN", 200)))
        side = d1_trend(d1_df, d1_sma)

        params = LevelsParams(days_window=days, include_inside_days=include_inside)
        raw = working_levels_d1(d1_df, side=side, params=params)

        out: List[Level] = []
        for L in raw:
            kind = str(getattr(L, "kind", "")).upper() or "PDH"
            out.append(Level(
                id=f"{symbol}:{kind}:{float(getattr(L,'price', 0.0)):.8f}",
                symbol=symbol,
                price=float(getattr(L, "price", 0.0)),
                kind=kind,
                scope="D1"
            ))

        # Ручные уровни — всегда добавляем (торгуются независимо от трендов).
        manual = parse_add_levels(env) or {}
        for k, px_list in manual.items():
            if symbol.upper().startswith(k.upper()):
                for px in (px_list if isinstance(px_list, (list, tuple)) else [px_list]):
                    out.append(Level(
                        id=f"{symbol}:MANUAL:{float(px):.8f}",
                        symbol=symbol,
                        price=float(px),
                        kind="MANUAL",
                        scope="H4"
                    ))

        self._cache[key] = out
        return out
