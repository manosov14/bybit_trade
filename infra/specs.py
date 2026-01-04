from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Tuple


def base_of(symbol: str) -> str:
    """Extract BASE from ccxt symbol.

    Examples:
      - BTC/USDT:USDT -> BTC
      - ETH/USDT -> ETH
      - BTC -> BTC
    """
    s = (symbol or "").strip()
    if not s:
        return ""
    return s.split("/")[0].split(":")[0].upper()


def _split_map(raw: str | None) -> Tuple[dict[str, float], dict[str, float]]:
    """Parse a map string into (full_symbol_map, base_map).

    Accepts entries separated by commas.
    Each entry is "<KEY>:<VALUE>", where KEY may contain ':' itself.

    Examples:
      - "BTC:0.5,ETH:0.05" => base_map
      - "BTC/USDT:USDT:0.5" => full_symbol_map
    """
    full: dict[str, float] = {}
    base: dict[str, float] = {}
    if not raw:
        return full, base
    for item in str(raw).split(","):
        part = item.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        try:
            left, right = part.rsplit(":", 1)
            val = float(str(right).replace(",", ".").strip())
            key = left.strip()
            if not key:
                continue
            if "/" in key:
                # Normalize the most common short form: BTC/USDT -> BTC/USDT:USDT
                k = key.upper()
                if ":" not in k and k.endswith("/USDT"):
                    k = k + ":USDT"
                full[k] = val
            else:
                base[key.upper()] = val
        except Exception:
            continue
    return full, base


@dataclass(frozen=True)
class SpecsResolver:
    """Resolves tick/qty steps for a symbol from env-like dict."""

    tick_map_raw: str | None
    qty_map_raw: str | None
    default_tick: float = 0.01
    default_step: float = 0.001

    def tick_size(self, symbol: str) -> float:
        full, base = _split_map(self.tick_map_raw)
        s = (symbol or "").strip().upper()
        b = base_of(symbol)
        return float(full.get(s) or base.get(b) or self.default_tick)

    def qty_step(self, symbol: str) -> float:
        full, base = _split_map(self.qty_map_raw)
        s = (symbol or "").strip().upper()
        b = base_of(symbol)
        return float(full.get(s) or base.get(b) or self.default_step)


def round_down(value: float, step: float) -> float:
    """Round value down to a given step (Decimal-safe)."""
    if step <= 0:
        return float(value)
    q = Decimal(str(step))
    v = Decimal(str(value))
    return float((v / q).to_integral_value(rounding=ROUND_DOWN) * q)
