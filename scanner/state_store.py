from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime, timedelta, timezone
import json, os


@dataclass
class SignalState:
    status: str = 'IDLE'            # IDLE | ARMED | COOL_DOWN
    attempts: int = 0
    penetrated_at: Optional[str] = None   # ISO ts: first penetration time
    deadline_iso: Optional[str] = None    # ISO: window for return (N*5m)
    reentry_until: Optional[str] = None   # ISO: H1 bar end + 1h
    cooldown_until: Optional[str] = None  # ISO: when to lift COOL_DOWN
    # extended fields for analytics / logging
    returned_at: Optional[str] = None     # ISO: when price returned into allowed range
    return_time_sec: Optional[float] = None  # seconds between penetration and return
    used: bool = False                    # explicitly mark level as fully processed


class SignalStateStore:
    def __init__(self, path: str = None, env_path: str = '.env'):
        log_dir = os.environ.get('LOG_DIR', 'logs')
        os.makedirs(log_dir, exist_ok=True)
        self.path = path or os.path.join(log_dir, 'state_scanner.json')
        self.env_path = env_path
        self.data: Dict[str, dict] = self._load()

    # ---- persistence ----
    def _load(self) -> Dict[str, dict]:
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self) -> None:
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, self.path)

    # ---- accessors ----
    def key(self, symbol: str, level_id: str) -> str:
        return f"{symbol}|{level_id}"

    def get(self, symbol: str, level_id: str) -> dict:
        return self.data.setdefault(self.key(symbol, level_id), SignalState().__dict__)

    # ---- lifecycle helpers ----
    def set_armed(self, symbol: str, level_id: str, return_bars_5m: int) -> None:
        """Mark signal as ARMED at the moment of first penetration.

        Also sets the deadline window when price is expected to return into range.
        """
        st = self.get(symbol, level_id)
        now = datetime.now(timezone.utc)
        st['status'] = 'ARMED'
        st['penetrated_at'] = now.isoformat()
        st['deadline_iso'] = (now + timedelta(minutes=5 * return_bars_5m)).isoformat()
        # reset return metrics on new penetration
        st['returned_at'] = None
        st['return_time_sec'] = None
        self.save()

    def set_cooldown_until(self, symbol: str, level_id: str, until_iso: str) -> None:
        st = self.get(symbol, level_id)
        st['status'] = 'COOL_DOWN'
        st['cooldown_until'] = until_iso
        self.save()

    def set_cooldown(self, symbol: str, level_id: str) -> None:
        st = self.get(symbol, level_id)
        st['status'] = 'COOL_DOWN'
        self.save()

    def set_idle(self, symbol: str, level_id: str) -> None:
        st = self.get(symbol, level_id)
        st['status'] = 'IDLE'
        self.save()

    def inc_attempt(self, symbol: str, level_id: str) -> int:
        st = self.get(symbol, level_id)
        st['attempts'] = int(st.get('attempts', 0)) + 1
        self.save()
        return st['attempts']

    def record_stop(self, symbol: str, level_id: str, h1_bar_ts_ms: int) -> None:
        """Call this from fills/PNL hook when SL happens.

        Re-entry window lasts until end_of_H1 bar + 1h; status becomes IDLE.
        """
        end_of_h1 = datetime.fromtimestamp(h1_bar_ts_ms / 1000, tz=timezone.utc).replace(
            minute=59, second=59, microsecond=0
        )
        re_until = end_of_h1 + timedelta(hours=1)
        st = self.get(symbol, level_id)
        st['status'] = 'IDLE'
        st['reentry_until'] = re_until.isoformat()
        st['attempts'] = int(st.get('attempts', 0))
        self.save()

    def record_return(self, symbol: str, level_id: str) -> Optional[float]:
        """Call this when price returns into the allowed range after penetration.

        Returns the computed return time in seconds (or None on parse error).
        """
        st = self.get(symbol, level_id)
        now = datetime.now(timezone.utc)
        st['returned_at'] = now.isoformat()
        ret_sec: Optional[float] = None
        pen = st.get('penetrated_at')
        if pen:
            try:
                t0 = datetime.fromisoformat(pen)
                ret_sec = (now - t0).total_seconds()
            except Exception:
                ret_sec = None
        st['return_time_sec'] = ret_sec
        self.save()
        return ret_sec

    def mark_used(self, symbol: str, level_id: str) -> None:
        """Mark level as fully processed so it will not generate new signals.

        This should be called after the trading lifecycle for this level is
        considered complete (e.g. after TP, or when you want to permanently
        ignore this level).
        """
        st = self.get(symbol, level_id)
        st['used'] = True
        # move to IDLE/Cooldown-neutral state
        st['status'] = 'IDLE'
        self.save()

    # ---- policy helpers ----
    def can_consider(self, symbol: str, level_id: str) -> bool:
        st = self.get(symbol, level_id)
        now = datetime.now(timezone.utc)

        # once marked as used -> never consider again
        if bool(st.get('used')):
            return False

        # Respect COOL_DOWN until expiry (if set)
        if st.get('status') == 'COOL_DOWN':
            cd = st.get('cooldown_until')
            try:
                if cd and now <= datetime.fromisoformat(cd):
                    return False
            except Exception:
                return False
            # cooldown expired -> reset
            st['status'] = 'IDLE'
            st['cooldown_until'] = None
            self.save()

        # If ARMED and return window is still active -> do not reconsider
        dl = st.get('deadline_iso')
        if st.get('status') == 'ARMED' and dl:
            try:
                if now <= datetime.fromisoformat(dl):
                    return False
            except Exception:
                return False
            # deadline passed -> reset
            st['status'] = 'IDLE'
            st['deadline_iso'] = None
            self.save()

        return True

    def can_reenter(self, symbol: str, level_id: str, max_attempts: int) -> bool:
        st = self.get(symbol, level_id)
        if bool(st.get('used')):
            return False
        if int(st.get('attempts', 0)) >= max_attempts:
            return False
        ru = st.get('reentry_until')
        if ru:
            try:
                return datetime.now(timezone.utc) <= datetime.fromisoformat(ru)
            except Exception:
                return False
        # if no explicit reentry window set -> no special allowance
        return False  # re-entry allowed only inside window when defined
