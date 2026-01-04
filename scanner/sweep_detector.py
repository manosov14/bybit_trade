from __future__ import annotations
from typing import Optional
import pandas as pd
from indicators.ta import atr
from .contracts import Level, SweepEvent

class SweepDetector:
    def __init__(self, atr_h1_len:int=14, min_atr_frac:float=0.05, max_atr_frac:float=0.35):
        self.atr_h1_len = int(atr_h1_len)
        self.min_atr_frac = float(min_atr_frac)
        self.max_atr_frac = float(max_atr_frac)

    @staticmethod
    def _ts_to_int(v) -> int:
        try:
            ts = pd.to_datetime(v, utc=True, errors="coerce")
            return 0 if ts is None or str(ts)=="NaT" else int(ts.value // 10**9)
        except Exception:
            return 0

    def detect(self, symbol:str, level:Level, m1_df:pd.DataFrame, h1_df:pd.DataFrame) -> Optional[SweepEvent]:
        """Detects any fact of level cross on M1.

        На этом шаге задача только зафиксировать сам прокол уровня:
        - свеча M1 должна пересечь цену уровня хотя бы на один тик (этот тик‑гард
          дополнительно проверяется выше по пайплайну в ModularScanner);
        - глубина в ATR(H1) здесь НЕ отфильтровывается, а только считается как справочная величина.

        Все дальнейшие фильтры (допустимая глубина, время возврата к уровню и т.п.)
        применяются на следующих этапах работы сканера.
        """
        # базовые проверки наличия данных
        if m1_df is None or len(m1_df) < 1 or h1_df is None or len(h1_df) < 2:
            return None

        # берём последнюю M1‑свечу
        last = m1_df.iloc[-1]
        try:
            hi = float(last["high"]); lo = float(last["low"])
        except Exception:
            # fallback для альтернативных имён колонок
            hi = float(last.get("HI", last.get("h", 0.0)))
            lo = float(last.get("LO", last.get("l", 0.0)))

        lvl = float(level.price)

        # если весь бар строго ниже уровня или строго выше уровня — это НЕ прокол
        if hi <= lvl and lo <= lvl:
            # полностью под уровнем — цена к уровню даже не подходила
            return None
        if hi >= lvl and lo >= lvl:
            # полностью над уровнем — цена всё время выше уровня
            return None

        # диапазон бара пересекает уровень — считаем глубины выше и ниже уровня
        up_depth = max(0.0, hi - lvl)   # насколько ушли ВЫШЕ уровня
        dn_depth = max(0.0, lvl - lo)   # насколько ушли НИЖЕ уровня

        if up_depth <= 0.0 and dn_depth <= 0.0:
            # на всякий случай: численно пересечения нет
            return None

        # выбираем сторону по более глубокому проколу:
        # если сильнее ушли вверх — это свип для шорта, если вниз — для лонга
        if up_depth > dn_depth:
            side = "SHORT"
            extreme = hi
            depth_abs = up_depth
        else:
            side = "LONG"
            extreme = lo
            depth_abs = dn_depth

        # глубину в ATR(H1) сейчас считаем ТОЛЬКО как информацию,
        # фильтрацию по min/max глубине переносим на более поздний этап.
        frac = 0.0
        try:
            h1 = h1_df.copy()
            h1["atr"] = atr(h1, self.atr_h1_len)
            a = float(h1["atr"].iloc[-1] or 0.0)
            if a and a > 0:
                frac = depth_abs / a
        except Exception:
            # если ATR посчитать не удалось, просто оставляем frac = 0.0
            pass

        ts_int = self._ts_to_int(last.get("ts") or last.get("timestamp") or last.get("time"))
        return SweepEvent(
            symbol=symbol,
            level_id=level.id,
            level_price=lvl,
            side=side,
            extreme_price=float(extreme),
            depth_abs=float(depth_abs),
            depth_atr_h1=float(frac),
            m1_bar_ts=int(ts_int),
        )
