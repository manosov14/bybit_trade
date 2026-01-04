
# bybit/usecases/levels_resolver.py
from typing import List, Any, Dict
import inspect
import re
import domain.levels as levels_mod

def _sym_variants(symbol:str)->List[str]:
    s = symbol.strip()
    v = {s, s.upper(), s.replace(" ", "")}
    for suf in (":USDT", ":USD", ":PERP"):
        if suf in s:
            v.add(s.replace(suf, ""))
    v.add(s.replace("/", ""))
    for suf in (":USDT", ":USD", ":PERP"):
        v.add(s.replace(suf, "").replace("/", ""))
    v.add(re.sub(r"[/:\s]", "", s))
    v.add(re.sub(r"[/:\s]", "_", s))
    return list(v)

def _is_level(obj: Any) -> bool:
    return hasattr(obj, "price") and hasattr(obj, "side")

def _is_level_list(obj: Any) -> bool:
    if isinstance(obj, list):
        return (len(obj) == 0) or _is_level(obj[0])
    return False

def _maybe_call(fn, *args):
    try:
        return fn(*args)
    except TypeError:
        return None
    except Exception:
        return None

def _try_rebuild_levels(env:Dict[str,str]):
    builder_names = (
        "build_levels","build_today_levels","compute_levels","compute_today_levels",
        "prepare_levels","prepare_today_levels","recalc_levels","recalc_today_levels",
        "init_levels","init_today_levels","load_levels","load_today_levels"
    )
    for name in builder_names:
        fn = getattr(levels_mod, name, None)
        if callable(fn):
            out = _maybe_call(fn, env)
            if out is None:
                _maybe_call(fn)

def resolve_levels(env:Dict[str,str], symbol:str)->List[Any]:
    _try_rebuild_levels(env)

    func_names = [
        "get_levels_for_symbol","levels_for_symbol","levels_for","get_levels","levels",
        "get_today_levels_for","today_levels_for","get_valid_levels_for_symbol",
        "get_levels_for","get_levels_today","daily_levels_for",
    ]
    for name in func_names:
        fn = getattr(levels_mod, name, None)
        if callable(fn):
            for args in ((symbol,env), (symbol,), (env,), ()):
                res = _maybe_call(fn, *args)
                if _is_level_list(res):
                    return res

    for name, fn in inspect.getmembers(levels_mod, inspect.isfunction):
        if "level" in name.lower():
            for args in ((symbol,env), (symbol,), (env,), ()):
                res = _maybe_call(fn, *args)
                if _is_level_list(res):
                    return res

    dict_names = ("LEVELS","levels_cache","TODAY_LEVELS","LEVELS_BY_SYMBOL","CACHE","CACHE_LEVELS")
    syms = _sym_variants(symbol)
    for dn in dict_names:
        obj = getattr(levels_mod, dn, None)
        if isinstance(obj, dict):
            for sv in syms:
                val = obj.get(sv)
                if _is_level_list(val):
                    return val if isinstance(val, list) else []

    _try_rebuild_levels(env)
    for dn in dict_names:
        obj = getattr(levels_mod, dn, None)
        if isinstance(obj, dict):
            for sv in syms:
                val = obj.get(sv)
                if _is_level_list(val):
                    return val if isinstance(val, list) else []

    return []
