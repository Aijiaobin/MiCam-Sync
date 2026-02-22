from __future__ import annotations

import logging
import os
import threading
import time

from micam_sync.config import RuntimeEnv
from micam_sync.db import StateDB


class InboxScanner(threading.Thread):
    def __init__(self, runtime: RuntimeEnv, db: StateDB, stop_event: threading.Event) -> None:
        super().__init__(daemon=True, name="inbox-scanner")
        self.runtime = runtime
        self.db = db
        self.stop_event = stop_event
        self.log = logging.getLogger("micam_sync.scanner")
        self._inbox_real = os.path.realpath(self.runtime.inbox_path)

    def _normalize_rel(self, full_path: str) -> str:
        rel = os.path.relpath(full_path, self._inbox_real)
        normalized = os.path.normpath(rel).replace("\\", "/")
        if normalized in {"", "."}:
            raise ValueError("invalid empty relative path")
        if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
            raise ValueError(f"invalid relative path: {normalized}")
        return normalized

    def _safe_realpath(self, full_path: str) -> str:
        real_path = os.path.realpath(full_path)
        if real_path != self._inbox_real and not real_path.startswith(f"{self._inbox_real}{os.sep}"):
            raise ValueError(f"path escapes inbox: {full_path}")
        return real_path

    def run(self) -> None:
        self.log.info("scanner started, inbox=%s", self.runtime.inbox_path)
        while not self.stop_event.is_set():
            started = time.time()
            seen_paths: set[str] = set()
            try:
                for root, _dirs, files in os.walk(self._inbox_real, followlinks=False):
                    for filename in files:
                        full_path = os.path.join(root, filename)
                        try:
                            real_path = self._safe_realpath(full_path)
                            stat = os.stat(real_path)
                        except (FileNotFoundError, ValueError):
                            continue

                        if not os.path.isfile(real_path) or os.path.islink(real_path):
                            continue

                        rel = self._normalize_rel(real_path)
                        seen_paths.add(rel)
                        self.db.observe_file(rel_path=rel, size=stat.st_size, mtime=stat.st_mtime, now_ts=time.time())
                self.db.mark_missing_not_seen(seen_paths)
            except Exception as exc:
                self.log.exception("scan failed: %s", exc)

            cfg = self.db.get_config()
            interval = int(cfg["scan_interval_seconds"])
            elapsed = time.time() - started
            self.stop_event.wait(max(0.0, interval - elapsed))

        self.log.info("scanner stopped")
