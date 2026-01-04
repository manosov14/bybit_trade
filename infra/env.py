# -*- coding: utf-8 -*-
"""
Robust .env loader with:
- UTF-8 support
- inline comments (#) allowed
- default values
- type casting helpers
- blank value handling
"""
from __future__ import annotations
import os, re, pathlib
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]

def _parse_env_lines(raw: str):
    data = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//") or s.startswith(";"):
            continue
        # keep part before inline comment if not within quotes
        if "#" in s and not re.search(r'["\'].*#.*["\']', s):
            s = s.split("#", 1)[0].rstrip()
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        # strip wrapping quotes
        if len(v) >= 2 and ((v[0] == v[-1]) and v[0] in ("'", '"')):
            v = v[1:-1]
        data[k] = v
    return data

def load_env(file_name: str = ".env") -> dict:
    path = ROOT / file_name if not os.path.isabs(file_name) else pathlib.Path(file_name)
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8", errors="ignore")
    env = _parse_env_lines(raw)
    env = normalize_env(env)
    env = _maybe_autofill_specs(env)
    return env

# Casting helpers
def as_bool(s: str | None, default=False) -> bool:
    if s is None or s == "":
        return default
    return str(s).strip().lower() in {"1","true","yes","on","y"}

def as_float(s: str | None, default: float = 0.0) -> float:
    try:
        return float(str(s).replace(",", ".")) if s not in (None, "") else default
    except Exception:
        return default

def as_int(s: str | None, default: int = 0) -> int:
    try:
        return int(float(str(s).replace(",", "."))) if s not in (None, "") else default
    except Exception:
        return default

def as_list(s: str | None, sep=",") -> list[str]:
    if not s:
        return []
    return [x.strip() for x in str(s).split(sep) if x.strip()]

def parse_price_map(raw: str | None) -> dict[str, float]:
    """Parses maps like
       - "BTC/USDT:USDT:0.5,ETH/USDT:USDT:0.05" or
       - "BTC:0.5,ETH:0.05"
       Returns {"BTC": 0.5, "ETH": 0.05}
    """
    out: dict[str, float] = {}
    if not raw:
        return out
    for item in str(raw).split(','):
        part = item.strip()
        if not part:
            continue
        try:
            tok = part.split(':')
            val = float(str(tok[-1]))
            left = ':'.join(tok[:-1])
            base = left.split('/')[0].split(':')[0].strip().upper()
            if base:
                out[base] = val
        except Exception:
            continue
    return out


# Defaults used by strategy (aligned with spec)
DEFAULTS = {
    "USE_SPEED": "1",
    "SPEED_ATR_TF": "1h",
    "SPEED_ATR_PERIOD": "14",
    "SPEED_LOOKBACK_H1": "6",          # how many H1 bars define impulse distance
    "SPEED_ACCEPT_FROM": "1.0",        # accept if ratio >= this
    "SPEED_ACCEPT_TO": "3.0",          # and <= this (configurable window)
}

def get(key: str, default: str | None = None) -> str | None:
    env = load_env()
    if key in env:
        return normalize_env(env)[key]
    if default is not None:
        return default
    return DEFAULTS.get(key)

def require(keys: list[str]):
    env = load_env()
    missing = [k for k in keys if env.get(k, "") == ""]
    return missing




def parse_add_levels(env: dict) -> dict:
    """
    Parse manual levels from env.

    Supports multiple levels per base, separated by ';' or ','.
    Accepts entries like:
      - BTC:68000; BTC:70000
      - BTC/USDT:USDT:68000;ETH/USDT:3600
    Returns dict like {"BTC": [68000.0, 70000.0], "ETH": [3600.0]}
    Keys are BASE symbols to match /levels table.
    """
    raw = str(env.get("ADD_LEVELS") or env.get("ADD_LEVEL") or "").strip()
    if not raw:
        return {}
    items = re.split(r"[;,]+", raw)
    out: dict[str, list[float]] = {}
    for it in items:
        it = it.strip()
        if not it:
            continue
        parts = re.split(r"[:=]", it)
        # pick the last numeric token as price
        price = None
        for token in reversed(parts):
            try:
                price = float(str(token).replace(",", "."))
                break
            except Exception:
                continue
        if price is None:
            continue
        # choose a symbol candidate (either BASE like 'ETH' or 'BTC/USDT:USDT')
        symbol = None
        for token in parts:
            t = token.strip().upper()
            if not t:
                continue
            if "/" in t or re.match(r"^[A-Z0-9]{2,}$", t):
                symbol = t
                break
        if not symbol:
            continue
        base = symbol.split('/')[0].split(':')[0].strip().upper()
        out.setdefault(base, []).append(float(price))
    # Deduplicate and sort values for each base
    for k in list(out.keys()):
        uniq = sorted(set(out[k]))
        out[k] = uniq
    return out



def normalize_env(env: dict) -> dict:
    """
    Normalize legacy/alias keys to canonical ones to keep /param output consistent.
    Canonical names:
      - MIN_SWEEP_ATR_H1, MAX_SWEEP_ATR_H1
      - USE_SPEED, SPEED_ACCEPT_FROM, SPEED_ACCEPT_TO, SPEED_LOOKBACK_H1
      - SLIPPAGE_CANCEL_MINUTES
      - MAX_OPEN_TRADES, STOP_SERIES_LIMIT
      - RETURN_BARS_5M, ENTRY_TICKS, STOP_TICKS, RR
      - D1_SMA_LEN, H4_SMA_LEN
      - SESSIONS (comma-separated)
    """
    e = dict(env) if env else {}
    aliases = {
        "MIN_SWEEP_ATR_H1": ["MIN_SWEEP_ATR_H1","MIN_SWEEP_ATR_H1","MIN_SWEEP_ATR_H1","MIN_SWEEP_ATR_H1"],
        "MAX_SWEEP_ATR_H1": ["MAX_SWEEP_ATR_H1","MAX_SWEEP_ATR_H1","MAX_SWEEP_ATR_H1","MAX_SWEEP_ATR_H1"],
        "USE_SPEED": ["USE_SPEED","USE_SPEED"],
        "SPEED_ACCEPT_FROM": ["SPEED_ACCEPT_FROM","SPEED_ACCEPT_FROM","SPEED_ACCEPT_FROM"],
        "SPEED_ACCEPT_TO": ["SPEED_ACCEPT_TO","SPEED_ACCEPT_TO","SPEED_ACCEPT_TO"],
        "SPEED_LOOKBACK_H1": ["SPEED_LOOKBACK_H1","SPEED_LOOKBACK_H1"],
        "SLIPPAGE_CANCEL_MINUTES": ["SLIPPAGE_CANCEL_MINUTES","SLIPPAGE_CANCEL_MINUTES","SLIPPAGE_CANCEL_MINUTES"],
        "MAX_OPEN_TRADES": ["MAX_OPEN_TRADES","MAX_OPEN_TRADES"],
        "STOP_SERIES_LIMIT": ["STOP_SERIES_LIMIT","STOP_SERIES_LIMIT"],
        "RETURN_BARS_5M": ["RETURN_BARS_5M","RETURN_BARS_5M","RETURN_BARS_5M"],
        "ENTRY_TICKS": ["ENTRY_TICKS","ENTRY_TICKS"],
        "STOP_TICKS": ["STOP_TICKS","STOP_TICKS"],
        "RR": ["RR","RR"],
        "D1_SMA_LEN": ["D1_SMA_LEN","D1_SMA_LEN","D1_SMA_LEN"],
        "H4_SMA_LEN": ["H4_SMA_LEN","H4_SMA_LEN","H4_SMA_LEN"],
        "SESSIONS": ["SESSIONS","SESSIONS"],
    }
    for canon, alist in aliases.items():
        if str(e.get(canon, "")).strip() == "":
            for a in alist:
                v = e.get(a, "")
                if str(v).strip() != "":
                    e[canon] = v
                    break
    e.setdefault("MIN_SWEEP_ATR_H1", "0.10")
    e.setdefault("MAX_SWEEP_ATR_H1", "0.35")
    e.setdefault("ATR_M5_LEN", "14")
    e.setdefault("ATR_H1_LEN", "14")
    e.setdefault("USE_SPEED", "false")
    e.setdefault("SPEED_ACCEPT_FROM", "0.5")
    e.setdefault("SPEED_ACCEPT_TO", "3.0")
    e.setdefault("SPEED_LOOKBACK_H1", "6")
    e.setdefault("SLIPPAGE_CANCEL_MINUTES", "5")
    e.setdefault("MAX_OPEN_TRADES", "2")
    e.setdefault("STOP_SERIES_LIMIT", "3")
    e.setdefault("RETURN_BARS_5M", "2")
    e.setdefault("ENTRY_TICKS", "2")
    e.setdefault("STOP_TICKS", "2")
    e.setdefault("RR", "3")
    e.setdefault("D1_SMA_LEN", "200")
    e.setdefault("H4_SMA_LEN", "50")
    e.setdefault("ENABLE_D1", "true")
    e.setdefault("ENABLE_H4", "true")
    e.setdefault("MAX_ATTEMPTS_PER_SIGNAL", "2")
    e.setdefault("PLACE_STOP_MARKET", "true")
    e.setdefault("STOP_BY_SWEEP", "true")
    return e

# === Auto-fetch tick/qty specs from Bybit and cache locally ==================
def _symbol_to_bybit(sym: str) -> str:
    # Project symbols like 'BTC/USDT:USDT' -> 'BTCUSDT'
    base = sym.split('/')[0].split(':')[0].strip().upper()
    return f"{base}USDT"

def _read_cache(path: str) -> dict:
    try:
        import json, os
        if os.path.exists(path):
            return json.loads(open(path, 'r', encoding='utf-8').read())
    except Exception:
        pass
    return {}

def _write_cache(path: str, data: dict) -> None:
    try:
        import json, os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'w', encoding='utf-8').write(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass

def _fetch_specs_bybit_linear(symbol_ccy: str) -> tuple[float, float] | None:
    try:
        import requests
        url = "https://api.bybit.com/v5/market/instruments-info"
        params = {"category": "linear", "symbol": symbol_ccy}
        r = requests.get(url, params=params, timeout=6)
        j = r.json()
        lst = (((j or {}).get("result") or {}).get("list") or [])
        if not lst:
            return None
        it = lst[0]
        tick = float(it["priceFilter"]["tickSize"])
        step = float(it["lotSize"]["qtyStep"])
        return tick, step
    except Exception:
        return None

def _maybe_autofill_specs(env: dict) -> dict:
    if not as_bool(env.get("AUTO_FETCH_SPEC", "false"), False):
        return env
    symbols = as_list(env.get("SYMBOLS", ""))
    ticks = parse_price_map(env.get("TICK_SIZE_MAP", ""))
    steps = parse_price_map(env.get("QTY_STEP_MAP", ""))
    cache_path = os.environ.get("LOG_DIR", "logs") + "/specs_cache.json"
    cache = _read_cache(cache_path)
    changed = False
    for s in symbols:
        base = s.split('/')[0].split(':')[0].strip().upper()
        if base in ticks and base in steps:
            continue
        # cache
        if base in cache:
            if base not in ticks and 'tick' in cache[base]:
                ticks[base] = float(cache[base]['tick'])
                changed = True
            if base not in steps and 'step' in cache[base]:
                steps[base] = float(cache[base]['step'])
                changed = True
            if base in ticks and base in steps:
                continue
        # fetch
        bybit_sym = _symbol_to_bybit(s)
        got = _fetch_specs_bybit_linear(bybit_sym)
        if got:
            t, st = got
            ticks[base] = t
            steps[base] = st
            cache[base] = {"tick": t, "step": st, "ts": datetime.now(timezone.utc).isoformat()}
            changed = True
    if changed:
        # write back into env strings (format: BASE:val)
        env["TICK_SIZE_MAP"] = ",".join([f"{k}:{ticks[k]}" for k in sorted(ticks.keys())])
        env["QTY_STEP_MAP"] = ",".join([f"{k}:{steps[k]}" for k in sorted(steps.keys())])
        _write_cache(cache_path, cache)
    return env
