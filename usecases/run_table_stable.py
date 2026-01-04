
from __future__ import annotations
from typing import Optional

class RunTable:
    """Стабильная ASCII-таблица: фиксированные ширины, единый заголовок."""
    def __init__(self):
        self.headers = ["Пара","Время пробоя","ATRx","Δ%","Тренд D1","Сессия","Вход","Стоп","Профит","PNL%","Результат"]
        self.widths = [12, 16, 6, 6, 8, 12, 12, 12, 12, 6, 10]
        self._printed_header = False

    @staticmethod
    def _fmt_num(v: Optional[float]) -> str:
        if v is None:
            return "—"
        try:
            return f"{float(v):.6f}".rstrip("0").rstrip(".")
        except Exception:
            return str(v)

    def header(self) -> str:
        top = "+" + "+".join("-"*(w+2) for w in self.widths) + "+"
        line = "|" + "|".join(" " + h.ljust(self.widths[i]) + " " for i,h in enumerate(self.headers)) + "|"
        return "\n".join([top, line, top])

    def row(self, symbol, ts, filters, entry, sl, tp, pnl, result):
        from datetime import timezone
        def fmt_time(t):
            if not t: return ""
            try: return t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
            except Exception: return str(t)
        atrx = str(filters.get("atrx_val",""))
        delt = str(filters.get("delta_val",""))
        trnd = str(filters.get("trend_val",""))
        sess = str(filters.get("sess_val",""))
        pnl_s = "—" if pnl is None else f"{float(pnl):.2f}%"
        data = [
            str(symbol), fmt_time(ts), atrx, delt, trnd, sess,
            self._fmt_num(entry), self._fmt_num(sl), self._fmt_num(tp), pnl_s, str(result or "—")
        ]
        out = []
        if not self._printed_header:
            self._printed_header = True
            out.append(self.header())
        line = "|" + "|".join(" " + str(data[i])[:self.widths[i]].ljust(self.widths[i]) + " " for i in range(len(self.headers))) + "|"
        out.append(line)
        return "\n".join(out)
