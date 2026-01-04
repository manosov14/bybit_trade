from __future__ import annotations
from infra.env import load_env
from usecases.run_tables import print_today_events_table

def run_day_events(env_path: str = ".env", log_dir: str | None = None) -> None:
    env = load_env(env_path)
    log_dir = log_dir or env.get("LOG_DIR","logs")
    print_today_events_table(log_dir, env)

if __name__ == "__main__":
    run_day_events()
