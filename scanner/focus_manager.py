from __future__ import annotations
from dataclasses import dataclass
import math
import pandas as pd
from indicators.ta import atr
from infra.env import load_env


@dataclass
class FocusManager:
    atr_m5_len: int = 14

    def in_focus(self, m5_df: pd.DataFrame, last_price: float, level_price: float) -> bool:
        """Return True if level is close enough to current 5m action to be scanned.

        Раньше использовалось только закрытие последней M1-свечи (last_price),
        из‑за чего глубокие проколы хвостом могли игнорироваться, если закрытие
        ушло далеко от уровня. Теперь сначала смотрим диапазон high/low последнего
        M5‑бара и только при необходимости откатываемся к last_price.
        """
        if m5_df is None or len(m5_df) < self.atr_m5_len + 1:
            return False

        m5 = m5_df.copy()
        m5["atr"] = atr(m5, self.atr_m5_len)
        a = float(m5["atr"].iloc[-1] or 0.0)
        if not (a and a > 0):
            return False

        env = load_env()
        mult = 1.0
        if isinstance(env, dict):
            try:
                mult = float(env.get("FOCUS_ATR_M5_MULT", env.get("FOCUS_WITHIN_ATR_M5", 1.0)))
            except Exception:
                mult = 1.0

        band = a * max(0.1, mult)

        # Попробуем использовать диапазон последней M5‑свечи.
        try:
            last_row = m5.iloc[-1]
            hi = float(
                last_row.get("high")
                or last_row.get("High")
                or last_row.get("H")
                or last_row.get("max")
            )
            lo = float(
                last_row.get("low")
                or last_row.get("Low")
                or last_row.get("L")
                or last_row.get("min")
            )
            if all(map(math.isfinite, (hi, lo))):
                lo_, hi_ = sorted((lo, hi))
                # уровень в расширенном коридоре последней свечи
                if (lo_ - band) <= level_price <= (hi_ + band):
                    return True
        except Exception:
            # если что-то пошло не так, используем старую логику по last_price
            pass

        # Фоллбек: старая логика по последней цене закрытия.
        if last_price is None or not math.isfinite(float(last_price)):
            return False
        return abs(float(last_price) - float(level_price)) <= band
