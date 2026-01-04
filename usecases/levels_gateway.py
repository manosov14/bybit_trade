# bybit/usecases/levels_gateway.py
from __future__ import annotations
from typing import Dict, List, Any, Iterable

try:
    import domain.levels as _levels_mod
except Exception:
    _levels_mod = None  # type: ignore

CACHE_NAMES: Iterable[str] = (
    "LEVELS_BY_SYMBOL", "LEVELS", "TODAY_LEVELS",
    "LEVELS_TODAY", "levels_cache", "CACHE_LEVELS"
)
QUOTES = ("USDT","USD","USDC","PERP")

def _canonical(sym: str) -> str:
    s = (sym or "").strip().upper().replace(" ", "").replace("/", "").replace(":", "").replace("-", "")
    for q in QUOTES:
        while s.endswith(q + q):
            s = s[:-len(q)]
    return s

def _find_cache() -> Dict[str, Any] | None:
    if _levels_mod is None:
        return None
    for name in CACHE_NAMES:
        obj = getattr(_levels_mod, name, None)
        if isinstance(obj, dict):
            return obj
    return None

def get_levels_snapshot(symbols: List[str]) -> Dict[str, List[Any]]:
    cache = _find_cache()
    out: Dict[str, List[Any]] = {s: [] for s in symbols}
    if not isinstance(cache, dict):
        return out
    index = {}
    for k, v in cache.items():
        if isinstance(v, list):
            index[_canonical(str(k))] = v
    for s in symbols:
        out[s] = list(index.get(_canonical(s), []))
    return out
