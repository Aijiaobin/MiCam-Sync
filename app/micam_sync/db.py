from __future__ import annotations

from contextlib import contextmanager
import os
import sqlite3
import threading
import time
from typing import Any, Iterator

from micam_sync.config import mutable_defaults_from_env, sanitize_config


class StateDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._write_lock = threading.Lock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._bootstrap()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
        finally:
            conn.close()

    def _bootstrap(self) -> None:
        with self._write_lock, self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rel_path TEXT NOT NULL UNIQUE,
                    size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    last_write_at REAL NOT NULL,
                    stable_count INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL CHECK (state IN ('pending','syncing','synced','failed','missing')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_retry_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    synced_at REAL
                );

                CREATE INDEX IF NOT EXISTS idx_files_state_next_retry ON files(state, next_retry_at);
                CREATE INDEX IF NOT EXISTS idx_files_last_write ON files(last_write_at);
                """
            )
            now_ts = time.time()
            defaults = mutable_defaults_from_env()
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                    (k, self._to_string(v), now_ts),
                )
            conn.commit()

    def _to_string(self, value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def get_config(self) -> dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        raw: dict[str, Any] = {}
        for row in rows:
            raw[row["key"]] = row["value"]
        return sanitize_config(raw)

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        merged = self.get_config()
        merged.update(updates)
        sanitized = sanitize_config(merged)
        now_ts = time.time()
        with self._write_lock, self._conn() as conn:
            for k, v in sanitized.items():
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                    (k, self._to_string(v), now_ts),
                )
            conn.commit()
        return sanitized

    def observe_file(self, rel_path: str, size: int, mtime: float, now_ts: float) -> None:
        with self._write_lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO files(rel_path, size, mtime, first_seen_at, last_seen_at, last_write_at, stable_count, state, attempts, next_retry_at, last_error)
                VALUES (?, ?, ?, ?, ?, ?, 0, 'pending', 0, 0, NULL)
                ON CONFLICT(rel_path) DO UPDATE SET
                    size = excluded.size,
                    mtime = excluded.mtime,
                    last_seen_at = excluded.last_seen_at,
                    last_write_at = CASE
                        WHEN files.size != excluded.size OR files.mtime != excluded.mtime OR files.state = 'missing' THEN excluded.last_write_at
                        ELSE files.last_write_at
                    END,
                    stable_count = CASE
                        WHEN files.size != excluded.size OR files.mtime != excluded.mtime OR files.state = 'missing' THEN 0
                        ELSE files.stable_count + 1
                    END,
                    state = CASE
                        WHEN files.size != excluded.size OR files.mtime != excluded.mtime OR files.state = 'missing' THEN 'pending'
                        ELSE files.state
                    END,
                    last_error = CASE
                        WHEN files.size != excluded.size OR files.mtime != excluded.mtime OR files.state = 'missing' THEN NULL
                        ELSE files.last_error
                    END,
                    next_retry_at = CASE
                        WHEN files.size != excluded.size OR files.mtime != excluded.mtime OR files.state = 'missing' THEN 0
                        ELSE files.next_retry_at
                    END
                """,
                (rel_path, size, mtime, now_ts, now_ts, now_ts),
            )
            conn.commit()

    def mark_missing_not_seen(self, seen_rel_paths: set[str]) -> None:
        with self._write_lock, self._conn() as conn:
            rows = conn.execute("SELECT id, rel_path FROM files WHERE state IN ('pending','failed','syncing')").fetchall()
            missing_ids = [row["id"] for row in rows if row["rel_path"] not in seen_rel_paths]
            if not missing_ids:
                return
            now_ts = time.time()
            conn.executemany(
                "UPDATE files SET state='missing', last_error='source file not found', next_retry_at=0, last_seen_at=? WHERE id=?",
                [(now_ts, file_id) for file_id in missing_ids],
            )
            conn.commit()

    def reset_stale_syncing(self) -> None:
        now_ts = time.time()
        with self._write_lock, self._conn() as conn:
            conn.execute(
                "UPDATE files SET state='failed', next_retry_at=?, last_error='recovered after restart' WHERE state='syncing'",
                (now_ts,),
            )
            conn.commit()

    def claim_due_file(self, delay_seconds: int) -> dict[str, Any] | None:
        now_ts = time.time()
        with self._write_lock, self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT id, rel_path, attempts
                FROM files
                WHERE state IN ('pending','failed')
                  AND stable_count >= 2
                  AND (? - last_write_at) >= ?
                  AND next_retry_at <= ?
                ORDER BY last_write_at ASC, id ASC
                LIMIT 1
                """,
                (now_ts, delay_seconds, now_ts),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None

            updated = conn.execute(
                "UPDATE files SET state='syncing' WHERE id=? AND state IN ('pending','failed')",
                (row["id"],),
            ).rowcount
            if updated != 1:
                conn.execute("ROLLBACK")
                return None

            conn.execute("COMMIT")
            return dict(row)

    def mark_synced(self, file_id: int) -> None:
        now_ts = time.time()
        with self._write_lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE files
                SET state='synced',
                    synced_at=?,
                    last_error=NULL,
                    next_retry_at=0
                WHERE id=?
                """,
                (now_ts, file_id),
            )
            conn.commit()

    def mark_failed(self, file_id: int, error: str, next_retry_at: float) -> None:
        with self._write_lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE files
                SET state='failed',
                    attempts=attempts+1,
                    last_error=?,
                    next_retry_at=?
                WHERE id=?
                """,
                (error[:2000], next_retry_at, file_id),
            )
            conn.commit()

    def mark_missing(self, file_id: int) -> None:
        with self._write_lock, self._conn() as conn:
            conn.execute(
                "UPDATE files SET state='missing', last_error='source file not found', next_retry_at=0 WHERE id=?",
                (file_id,),
            )
            conn.commit()

    def queue_stats(self) -> dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute("SELECT state, COUNT(*) AS c FROM files GROUP BY state ORDER BY state").fetchall()
            total = conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
            newest = conn.execute("SELECT MAX(synced_at) AS latest FROM files WHERE synced_at IS NOT NULL").fetchone()
        by_state = {row["state"]: row["c"] for row in rows}
        return {"total": total, "by_state": by_state, "last_synced_at": newest["latest"]}

    def list_files(self, limit: int = 100, state: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if state:
                rows = conn.execute(
                    """
                    SELECT rel_path, size, mtime, state, attempts, last_error, last_write_at, synced_at
                    FROM files
                    WHERE state=?
                    ORDER BY last_seen_at DESC
                    LIMIT ?
                    """,
                    (state, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT rel_path, size, mtime, state, attempts, last_error, last_write_at, synced_at
                    FROM files
                    ORDER BY last_seen_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def is_mount_ready(self, mount_path: str) -> bool:
        return os.path.ismount(mount_path)
