
from __future__ import annotations
import os
from infra.env import load_env
from usecases.run_tables import print_today_events_table, print_symbols_snapshot

def main():
    env = load_env(os.environ.get("ENV_PATH",".env"))
    log_dir = os.environ.get("LOG_DIR","logs")
    print_today_events_table(log_dir, env)
    state_path = os.path.join(log_dir, "state.json")
    print_symbols_snapshot(state_path, env)

if __name__ == "__main__":
    main()
