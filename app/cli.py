import argparse
import shlex
from app.command_bus import handle_command

import os, json, time
from datetime import datetime, timezone
try:
    from infra.env import load_env
    from infra.exchange_ccxt import ExchangeService
except Exception:
    # Fallback relative imports if layout differs
    from bybit.infra.env import load_env
    from bybit.infra.exchange_ccxt import ExchangeService

def _load_state(log_dir: str) -> dict:
    p = os.path.join(log_dir, "state.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"trades": {}}

def _save_state(log_dir: str, state: dict) -> None:
    p = os.path.join(log_dir, "state.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)

def _jlog(log_dir: str, event: str, **fields):
    try:
        os.makedirs(log_dir, exist_ok=True)
        p = os.path.join(log_dir, "events.jsonl")
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event}
        rec.update(fields)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

def reconcile(env_path: str):
    """
    Приводим локальный state.json в соответствие с биржей:
    - добиваем отсутствующие order_ids по clientOrderId
    - отмечаем пропавшие ордера (политика мягкая: не ставим заново, только фиксируем отсутствие)
    Вызывается перед /run.
    """
    env = load_env(env_path or ".env")
    symbols = [s.strip() for s in str(env.get("SYMBOLS", "BTC/USDT,ETH/USDT")).split(",") if s.strip()]
    log_dir = os.environ.get("LOG_DIR", "logs")

    # init exchange with TESTNET flag
    testnet = str(env.get("TESTNET", "false")).strip().lower() in ("1","true","yes","on","y")
    api_k, api_s = env.get("BYBIT_API_KEY", ""), env.get("BYBIT_API_SECRET", "")
    # If API keys are not set, skip reconcile (allows public-data / dry-run runs)
    if not (str(api_k).strip() and str(api_s).strip()):
        print("[reconcile] skipped: missing BYBIT_API_KEY/BYBIT_API_SECRET")
        _jlog(log_dir, "reconcile.skipped", stage="auth", reason="missing_keys")
        return

    ex = None
    try:
        ex = ExchangeService(api_k, api_s, testnet=testnet)  # our wrapper
    except Exception as e:
        print(f"[reconcile] exchange init failed: {e}")
        _jlog(log_dir, "reconcile.error", stage="init", error=str(e))
        return

    # collect open orders by clientOrderId across all symbols
    by_cid = {}
    try:
        for sym in symbols:
            try:
                oo = ex.ex.fetch_open_orders(sym)
                for o in oo or []:
                    cid = o.get("clientOrderId") or ""
                    if cid:
                        by_cid[str(cid)] = o
            except Exception as e:
                print(f"[reconcile] fetch_open_orders failed for {sym}: {e}")
                _jlog(log_dir, "reconcile.fetch_open_orders.error", symbol=sym, error=str(e))
    except Exception as e:
        print(f"[reconcile] open orders fetch error: {e}")
        _jlog(log_dir, "reconcile.error", stage="fetch", error=str(e))

    # update state
    state = _load_state(log_dir)
    trades = state.get("trades", {})
    changed = False
    fixed = 0
    for tid, tr in list(trades.items()):
        ids = tr.get("order_ids") or {}
        for kind in ("entry", "sl", "tp"):
            cid = f"{tid}-{kind}"
            if cid in by_cid and not ids.get(kind):
                ids[kind] = by_cid[cid].get("id")
                changed, fixed = True, fixed + 1
        tr["order_ids"] = ids

    if changed:
        _save_state(log_dir, state)
    _jlog(log_dir, "reconcile.done", fixed=fixed, trades=len(trades), symbols=len(symbols))
    print(f"[reconcile] done: fixed {fixed} order_ids in state.json")

ALLOWED = ("/param", "/trend", "/days", "/levels", "/run")

def _repl(env_path: str):
    print("Interactive mode. Commands: /param, /trend, /days [N], /levels, /run, /help, /quit")
    print(f"Using .env: {env_path}")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye"); break
        if not line:
            continue
        if line in ("/q","/quit","exit"):
            print("Bye"); break
        if line in ("/h","/help"):
            print("Commands: /param | /trend | /days [N] | /levels | /run | :env <path> | /quit")
            continue
        if line.startswith(":env"):
            parts = shlex.split(line)
            if len(parts) >= 2:
                env_path = parts[1]
                print(f"Switched .env -> {env_path}")
            else:
                print(f"Current .env: {env_path}")
            continue

        parts = shlex.split(line)
        cmd = parts[0]
        if cmd not in ALLOWED:
            print(f"Unknown command: {cmd}. Type /help"); continue

        if cmd == "/days":
            days = None
            if len(parts) >= 2:
                try:
                    days = int(parts[1])
                except ValueError:
                    print("Usage: /days [N]"); continue
            handle_command("/days", env_path=env_path, days=days)
        else:
            if cmd == "/run":
                try:
                    reconcile(env_path)
                except Exception as e:
                    print(f"[reconcile] skipped: {e}")
            handle_command(cmd, env_path=env_path)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", nargs="?", help="Command to run: /param, /trend, /days, /levels, /run")
    p.add_argument("--env", dest="env_path", default=".env")
    p.add_argument("--days", type=int, default=None)
    args = p.parse_args()

    if not args.command:
        _repl(args.env_path)
        return

    if args.command not in ALLOWED:
        raise SystemExit(f"Unknown command: {args.command}")

    if args.command == "/days":
        handle_command("/days", env_path=args.env_path, days=args.days)
    else:
        if args.command == "/run":
            try:
                reconcile(args.env_path)
            except Exception as e:
                print(f"[reconcile] skipped: {e}")
        handle_command(args.command, env_path=args.env_path)

if __name__ == "__main__":
    main()

def cmd_run_levels(env_path: str):
    # Legacy helper kept for compatibility; /levels command is handled in command_bus.
    from app.command_bus import handle_command
    handle_command("/levels", env_path=env_path)


def _param_grouped_print(env):
    print("— РЕЖИМ / ДОСТУПЫ —")
    print(f"MODE={env.get('MODE')}  TESTNET={env.get('TESTNET')}  SYMBOLS={env.get('SYMBOLS')}")
    print("— ТРЕНДЫ / ОКНО УРОВНЕЙ —")
    print(f"D1={env.get('D1')}  H4={env.get('H4')}  D1_SMA_LEN={env.get('D1_SMA_LEN')}  H4_SMA_LEN={env.get('H4_SMA_LEN')}  DAYS={env.get('DAYS')}  INCLUDE_INSIDE={env.get('INCLUDE_INSIDE')}")
    print("— СЕССИИ —")
    print(f"USE_SESSIONS={env.get('USE_SESSIONS')}  SESSIONS={env.get('SESSIONS')}")
    print("— ГЛУБИНА/ATR —")
    print(f"ATR_M5_LEN={env.get('ATR_M5_LEN')}  ATR_H1_LEN={env.get('ATR_H1_LEN')}  MIN_SWEEP_ATR_H1={env.get('MIN_SWEEP_ATR_H1')}  MAX_SWEEP_ATR_H1={env.get('MAX_SWEEP_ATR_H1')}  RETURN_BARS_5M={env.get('RETURN_BARS_5M')}")
    print("— СКОРОСТЬ —")
    print(f"USE_SPEED={env.get('USE_SPEED')}  SPEED_LOOKBACK_H1={env.get('SPEED_LOOKBACK_H1')}  SPEED_ACCEPT_FROM={env.get('SPEED_ACCEPT_FROM')}  SPEED_ACCEPT_TO={env.get('SPEED_ACCEPT_TO')}")
    print("— ВХОД / РИСК —")
    print(f"ENTRY_TICKS={env.get('ENTRY_TICKS')}  STOP_TICKS={env.get('STOP_TICKS')}  STOP_BY_SWEEP={env.get('STOP_BY_SWEEP')}  RR={env.get('RR')}")
    print(f"ACCOUNT_EQUITY={env.get('ACCOUNT_EQUITY')}  RISK_PCT={env.get('RISK_PCT')}  MAX_OPEN_TRADES={env.get('MAX_OPEN_TRADES')}  STOP_SERIES_LIMIT={env.get('STOP_SERIES_LIMIT')}  SLIPPAGE_CANCEL_MINUTES={env.get('SLIPPAGE_CANCEL_MINUTES')}")
    print("— ШАГИ РЫНКА (ТИК/ЛОТ) —")
    print(f"TICK_SIZE_MAP={env.get('TICK_SIZE_MAP')}  QTY_STEP_MAP={env.get('QTY_STEP_MAP')}  DEFAULT_TICK_SIZE={env.get('DEFAULT_TICK_SIZE')}  DEFAULT_QTY_STEP={env.get('DEFAULT_QTY_STEP')}")
    print("— ПРЕВЬЮ / ОТЛАДКА —")
    print(f"PREVIEW_TODAY_ON_RUN={env.get('PREVIEW_TODAY_ON_RUN')}  PREVIEW_TODAY_MAX_EVENTS={env.get('PREVIEW_TODAY_MAX_EVENTS')}  PREVIEW_SIGNALS_TABLE={env.get('PREVIEW_SIGNALS_TABLE')}  PREVIEW_IGNORE_TREND={env.get('PREVIEW_IGNORE_TREND')}")
    print("— ДАННЫЕ / TTL —")
    print(f"REFRESH_1D_SEC={env.get('REFRESH_1D_SEC')}  REFRESH_4H_SEC={env.get('REFRESH_4H_SEC')}  REFRESH_1H_SEC={env.get('REFRESH_1H_SEC')}  WARMUP_SWEEP_BARS={env.get('WARMUP_SWEEP_BARS')}  WARMUP_H1_BARS={env.get('WARMUP_H1_BARS')}  API_BACKOFF_SEC={env.get('API_BACKOFF_SEC')}")
    print("— РУЧНЫЕ УРОВНИ —")
    print(f"ADD_LEVELS={env.get('ADD_LEVELS')}")
