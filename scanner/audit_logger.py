from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict
from datetime import datetime, timezone, timedelta
import os, json, time, gzip, glob, shutil

@dataclass
class AuditLogger:
    log_dir: str = os.environ.get("LOG_DIR", "logs")
    jsonl_name: str = "events.jsonl"
    to_stdout: bool = True
    # rotation params (overridable via ENV)
    rotate_max_mb: float = float(os.environ.get("LOG_ROTATE_MAX_MB", 50))
    keep_days: int = int(os.environ.get("LOG_ROTATE_KEEP_DAYS", 14))
    compress_rotated: bool = str(os.environ.get("LOG_COMPRESS_ROTATED", "true")).lower() == "true"

    def __post_init__(self):
        os.makedirs(self.log_dir, exist_ok=True)
        self.jsonl_path = os.path.join(self.log_dir, self.jsonl_name)
        self._last_err: Dict[str, float] = {}
        # ensure file exists
        try:
            if not os.path.exists(self.jsonl_path):
                with open(self.jsonl_path, "a", encoding="utf-8") as _:
                    pass
        except Exception:
            pass

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _write_jsonl(self, rec: Dict[str, Any]) -> None:
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _rotate_if_needed(self) -> None:
        """Rotate events.jsonl when it exceeds rotate_max_mb."""
        try:
            max_bytes = int(self.rotate_max_mb * 1024 * 1024)
            size = os.path.getsize(self.jsonl_path) if os.path.exists(self.jsonl_path) else 0
            if size < max_bytes:
                return
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            base = os.path.join(self.log_dir, f"events-{stamp}.jsonl")
            if self.compress_rotated:
                gz = base + ".gz"
                # copy+truncate
                with open(self.jsonl_path, "rb") as src, gzip.open(gz, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                with open(self.jsonl_path, "w", encoding="utf-8"):
                    pass
            else:
                os.replace(self.jsonl_path, base)
                with open(self.jsonl_path, "a", encoding="utf-8"):
                    pass
        except Exception:
            pass

    def _cleanup_old(self) -> None:
        """Remove rotated archives older than keep_days."""
        try:
            if self.keep_days <= 0:
                return
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.keep_days)
            for pat in ("events-*.jsonl", "events-*.jsonl.gz"):
                for fp in glob.glob(os.path.join(self.log_dir, pat)):
                    name = os.path.basename(fp)
                    ts = None
                    try:
                        core = name.split(".", 1)[0]  # events-YYYYMMDD-HHMMSS
                        stamp = core.split("-", 1)[1]
                        ts = datetime.strptime(stamp, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
                    except Exception:
                        try:
                            ts = datetime.fromtimestamp(os.path.getmtime(fp), tz=timezone.utc)
                        except Exception:
                            ts = None
                    if ts and ts < cutoff:
                        try:
                            os.remove(fp)
                        except Exception:
                            pass
        except Exception:
            pass

    def event(self, etype: str, **payload):
        rec = {"ts": self._now(), "type": etype, **payload}
        print_ok = True
        if etype == "symbol_error":
            key = f"{etype}:{payload.get('symbol','?')}:{payload.get('error','')}"
            last = self._last_err.get(key, 0.0)
            if time.time() - last < 10:
                print_ok = False
            else:
                self._last_err[key] = time.time()
        if self.to_stdout and print_ok:
            msg = f"[{rec['ts']}] {etype} " + " ".join(f"{k}={v}" for k,v in payload.items())
            print(msg)
        # rotation + cleanup before write
        self._rotate_if_needed()
        self._cleanup_old()
        self._write_jsonl(rec)

    # Compatibility alias: earlier modules may call `log_event()`.
    def log_event(self, etype: str, **payload):
        """Alias for :meth:`event`.

        Keeps the logger API stable across refactors.
        """
        return self.event(etype, **payload)
