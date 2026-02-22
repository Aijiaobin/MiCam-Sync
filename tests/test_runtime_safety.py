import os
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from micam_sync.api import create_app
from micam_sync.config import RuntimeEnv
from micam_sync.db import StateDB
from micam_sync.sync_worker import SyncWorker


class ApiDbStub:
    def get_config(self):
        return {
            "sync_delay_seconds": 300,
            "scan_interval_seconds": 10,
            "target_subdir": "",
            "delete_after_sync": False,
            "sync_retry_base_seconds": 15,
            "sync_retry_max_seconds": 600,
            "sync_copy_chunk_bytes": 1048576,
            "setup_completed": False,
        }

    def is_mount_ready(self, _mount_path):
        return True

    def update_config(self, updates):
        cfg = self.get_config()
        cfg.update(updates)
        return cfg

    def queue_stats(self):
        return {"total": 0, "by_state": {}, "last_synced_at": None}

    def list_files(self, limit=100, state=None):
        return []


class RuntimeSafetyTests(unittest.TestCase):
    def test_db_bootstrap_uses_env_mutable_defaults(self):
        old_delay = os.environ.get("SYNC_DELAY_SECONDS")
        old_scan = os.environ.get("SCAN_INTERVAL_SECONDS")
        os.environ["SYNC_DELAY_SECONDS"] = "900"
        os.environ["SCAN_INTERVAL_SECONDS"] = "12"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db_path = os.path.join(tmp, "state.db")
                db = StateDB(db_path)
                cfg = db.get_config()
                self.assertEqual(cfg["sync_delay_seconds"], 900)
                self.assertEqual(cfg["scan_interval_seconds"], 12)
        finally:
            if old_delay is None:
                os.environ.pop("SYNC_DELAY_SECONDS", None)
            else:
                os.environ["SYNC_DELAY_SECONDS"] = old_delay
            if old_scan is None:
                os.environ.pop("SCAN_INTERVAL_SECONDS", None)
            else:
                os.environ["SCAN_INTERVAL_SECONDS"] = old_scan

    def test_api_requires_token_when_disallowed(self):
        runtime = RuntimeEnv(
            inbox_path="/tmp/inbox",
            state_db_path="/tmp/state.db",
            webdav_mount_path="/tmp/mount",
            api_host="127.0.0.1",
            api_port=8080,
            api_token="",
            api_allow_no_token=False,
            sync_max_workers=1,
        )
        app = create_app(runtime, ApiDbStub())
        client = app.test_client()

        res = client.get("/api/config")
        self.assertEqual(res.status_code, 503)

    def test_api_allows_no_token_when_enabled(self):
        runtime = RuntimeEnv(
            inbox_path="/tmp/inbox",
            state_db_path="/tmp/state.db",
            webdav_mount_path="/tmp/mount",
            api_host="127.0.0.1",
            api_port=8080,
            api_token="",
            api_allow_no_token=True,
            sync_max_workers=1,
        )
        app = create_app(runtime, ApiDbStub())
        client = app.test_client()

        res = client.get("/api/config")
        self.assertEqual(res.status_code, 200)

    def test_setup_endpoint_returns_smb_webdav_defaults(self):
        runtime = RuntimeEnv(
            inbox_path="/tmp/inbox",
            state_db_path="/tmp/state.db",
            webdav_mount_path="/tmp/mount",
            api_host="127.0.0.1",
            api_port=8080,
            api_token="",
            api_allow_no_token=True,
            sync_max_workers=1,
        )
        app = create_app(runtime, ApiDbStub())
        client = app.test_client()

        res = client.get("/api/setup")
        self.assertEqual(res.status_code, 200)
        payload = res.get_json()
        self.assertIn("smb", payload)
        self.assertIn("webdav", payload)
        self.assertIn("smb_password_configured", payload["smb"])
        self.assertIn("webdav_mount_path", payload["webdav"])

    def test_smb_test_endpoint_validates_payload(self):
        runtime = RuntimeEnv(
            inbox_path="/tmp/inbox",
            state_db_path="/tmp/state.db",
            webdav_mount_path="/tmp/mount",
            api_host="127.0.0.1",
            api_port=8080,
            api_token="",
            api_allow_no_token=True,
            sync_max_workers=1,
        )
        app = create_app(runtime, ApiDbStub())
        client = app.test_client()

        with tempfile.TemporaryDirectory() as tmp:
            runtime = RuntimeEnv(
                inbox_path=tmp,
                state_db_path="/tmp/state.db",
                webdav_mount_path="/tmp/mount",
                api_host="127.0.0.1",
                api_port=8080,
                api_token="",
                api_allow_no_token=True,
                sync_max_workers=1,
            )
            app = create_app(runtime, ApiDbStub())
            client = app.test_client()
            res = client.post(
                "/api/test/smb",
                json={
                    "smb_user": "micam",
                    "smb_password": "abc",
                    "smb_share_name": "MI_CAMERA",
                    "smb_min_protocol": "NT1",
                    "smb_max_protocol": "SMB3",
                    "smb_enable_lanman": "no",
                },
            )
            self.assertEqual(res.status_code, 200)
            self.assertTrue(res.get_json()["ok"])

    def test_mount_test_endpoint_requires_absolute_path(self):
        runtime = RuntimeEnv(
            inbox_path="/tmp/inbox",
            state_db_path="/tmp/state.db",
            webdav_mount_path="/tmp/mount",
            api_host="127.0.0.1",
            api_port=8080,
            api_token="",
            api_allow_no_token=True,
            sync_max_workers=1,
        )
        app = create_app(runtime, ApiDbStub())
        client = app.test_client()

        res = client.post("/api/test/mount", json={"webdav_mount_path": "relative/path"})
        self.assertEqual(res.status_code, 400)

    def test_claim_due_file_respects_delay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = StateDB(os.path.join(tmp, "state.db"))
            now = time.time()

            # Stable but too recent: should not be claimed yet.
            for _ in range(3):
                db.observe_file("recent.mp4", size=10, mtime=1.0, now_ts=now - 100)
            self.assertIsNone(db.claim_due_file(delay_seconds=300))

            # Stable and old enough: should be claimed.
            for _ in range(3):
                db.observe_file("due.mp4", size=10, mtime=1.0, now_ts=now - 400)
            claimed = db.claim_due_file(delay_seconds=300)
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["rel_path"], "due.mp4")

    def test_sync_worker_blocks_path_escape(self):
        runtime = RuntimeEnv(
            inbox_path="/tmp/inbox",
            state_db_path="/tmp/state.db",
            webdav_mount_path="/tmp/mount",
            api_host="127.0.0.1",
            api_port=8080,
            api_token="token",
            api_allow_no_token=False,
            sync_max_workers=1,
        )
        worker = SyncWorker(runtime=runtime, db=ApiDbStub(), stop_event=mock.MagicMock(), worker_name="sync-test")
        with self.assertRaises(ValueError):
            worker._safe_join_under_root("/tmp/mount", "../escape.txt")


if __name__ == "__main__":
    unittest.main()
