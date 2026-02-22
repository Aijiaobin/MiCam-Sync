from __future__ import annotations

import logging
import os
import threading
import time

from micam_sync.config import RuntimeEnv
from micam_sync.db import StateDB


class SyncWorker(threading.Thread):
    def __init__(self, runtime: RuntimeEnv, db: StateDB, stop_event: threading.Event, worker_name: str) -> None:
        super().__init__(daemon=True, name=worker_name)
        self.runtime = runtime
        self.db = db
        self.stop_event = stop_event
        self.log = logging.getLogger(f"micam_sync.{worker_name}")

        self._inbox_real = os.path.realpath(self.runtime.inbox_path)
        self._mount_real = os.path.realpath(self.runtime.webdav_mount_path)

    def _compute_backoff(self, attempts: int, base: int, maximum: int) -> int:
        return min(maximum, base * (2 ** min(8, attempts)))

    def _normalize_rel(self, rel_path: str) -> str:
        normalized = os.path.normpath(rel_path).replace("\\", "/")
        if normalized in {"", "."}:
            raise ValueError("invalid empty relative path")
        if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
            raise ValueError(f"invalid relative path: {rel_path}")
        return normalized

    def _safe_join_under_root(self, root_real: str, rel_path: str) -> str:
        full_path = os.path.realpath(os.path.join(root_real, rel_path))
        if full_path != root_real and not full_path.startswith(f"{root_real}{os.sep}"):
            raise ValueError(f"path escapes root: {rel_path}")
        return full_path

    def _copy_file(self, src: str, dst: str, chunk_bytes: int) -> None:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = f"{dst}.part"
        with open(src, "rb") as infile, open(tmp, "wb") as outfile:
            while True:
                block = infile.read(chunk_bytes)
                if not block:
                    break
                outfile.write(block)
            outfile.flush()
            os.fsync(outfile.fileno())
        os.replace(tmp, dst)

    def run(self) -> None:
        self.log.info("sync worker started, mount=%s", self.runtime.webdav_mount_path)
        while not self.stop_event.is_set():
            cfg = self.db.get_config()
            delay_seconds = int(cfg["sync_delay_seconds"])
            retry_base = int(cfg["sync_retry_base_seconds"])
            retry_max = int(cfg["sync_retry_max_seconds"])
            chunk_bytes = int(cfg["sync_copy_chunk_bytes"])
            delete_after_sync = bool(cfg["delete_after_sync"])
            target_subdir = str(cfg["target_subdir"])

            if not self.db.is_mount_ready(self.runtime.webdav_mount_path):
                self.log.warning("webdav mount not ready: %s", self.runtime.webdav_mount_path)
                self.stop_event.wait(3.0)
                continue

            task = self.db.claim_due_file(delay_seconds=delay_seconds)
            if not task:
                self.stop_event.wait(1.0)
                continue

            file_id = int(task["id"])
            rel = self._normalize_rel(str(task["rel_path"]))

            dst_root = self._mount_real
            if target_subdir:
                dst_root = self._safe_join_under_root(self._mount_real, target_subdir)

            src = self._safe_join_under_root(self._inbox_real, rel)
            dst = self._safe_join_under_root(dst_root, rel)

            try:
                if not os.path.exists(src):
                    self.db.mark_missing(file_id)
                    continue
                if not os.path.isfile(src):
                    raise ValueError(f"source is not regular file: {rel}")
                self._copy_file(src=src, dst=dst, chunk_bytes=chunk_bytes)
                if delete_after_sync:
                    try:
                        os.remove(src)
                    except FileNotFoundError:
                        pass
                self.db.mark_synced(file_id)
                self.log.info("synced %s", rel)
            except Exception as exc:
                attempts = int(task["attempts"]) + 1
                next_retry = time.time() + self._compute_backoff(attempts=attempts, base=retry_base, maximum=retry_max)
                self.db.mark_failed(file_id, str(exc), next_retry_at=next_retry)
                self.log.exception("sync failed (%s): %s", rel, exc)
                self.stop_event.wait(0.5)

        self.log.info("sync worker stopped")
