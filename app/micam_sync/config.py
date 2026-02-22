from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


def _env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    value = int(raw)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeEnv:
    inbox_path: str
    state_db_path: str
    webdav_mount_path: str
    api_host: str
    api_port: int
    api_token: str
    api_allow_no_token: bool
    sync_max_workers: int


MUTABLE_DEFAULTS: dict[str, Any] = {
    "sync_delay_seconds": 300,
    "scan_interval_seconds": 10,
    "target_subdir": "camera",
    "delete_after_sync": False,
    "sync_retry_base_seconds": 15,
    "sync_retry_max_seconds": 600,
    "sync_copy_chunk_bytes": 1024 * 1024,
    "setup_completed": False,
}


ALLOWED_FILE_STATES = {"pending", "syncing", "synced", "failed", "missing"}


def normalize_target_subdir(value: Any) -> str:
    normalized = os.path.normpath(str(value).strip().replace("\\", "/")).replace("\\", "/")
    if normalized in {"", ".", "/"}:
        return ""
    if os.path.isabs(normalized) or normalized.startswith("../") or normalized == "..":
        raise ValueError("target_subdir must be a relative path under mount root")
    return normalized.strip("/")


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def sanitize_config(raw: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(MUTABLE_DEFAULTS)
    cfg.update(raw)

    cfg["sync_delay_seconds"] = max(300, int(cfg["sync_delay_seconds"]))
    cfg["scan_interval_seconds"] = max(2, min(60, int(cfg["scan_interval_seconds"])))
    cfg["target_subdir"] = normalize_target_subdir(cfg["target_subdir"])
    cfg["delete_after_sync"] = parse_bool(cfg["delete_after_sync"])
    cfg["sync_retry_base_seconds"] = max(5, min(300, int(cfg["sync_retry_base_seconds"])))
    cfg["sync_retry_max_seconds"] = max(
        cfg["sync_retry_base_seconds"],
        min(3600, int(cfg["sync_retry_max_seconds"])),
    )
    cfg["sync_copy_chunk_bytes"] = max(64 * 1024, min(16 * 1024 * 1024, int(cfg["sync_copy_chunk_bytes"])))
    cfg["setup_completed"] = parse_bool(cfg["setup_completed"])
    return cfg


def mutable_defaults_from_env() -> dict[str, Any]:
    raw: dict[str, Any] = {
        "sync_delay_seconds": os.getenv("SYNC_DELAY_SECONDS", str(MUTABLE_DEFAULTS["sync_delay_seconds"])),
        "scan_interval_seconds": os.getenv("SCAN_INTERVAL_SECONDS", str(MUTABLE_DEFAULTS["scan_interval_seconds"])),
        "target_subdir": os.getenv("TARGET_SUBDIR", str(MUTABLE_DEFAULTS["target_subdir"])),
        "delete_after_sync": os.getenv("DELETE_AFTER_SYNC", str(MUTABLE_DEFAULTS["delete_after_sync"])),
        "sync_retry_base_seconds": os.getenv("SYNC_RETRY_BASE_SECONDS", str(MUTABLE_DEFAULTS["sync_retry_base_seconds"])),
        "sync_retry_max_seconds": os.getenv("SYNC_RETRY_MAX_SECONDS", str(MUTABLE_DEFAULTS["sync_retry_max_seconds"])),
        "sync_copy_chunk_bytes": os.getenv("SYNC_COPY_CHUNK_BYTES", str(MUTABLE_DEFAULTS["sync_copy_chunk_bytes"])),
        "setup_completed": os.getenv("SETUP_COMPLETED", str(MUTABLE_DEFAULTS["setup_completed"])),
    }
    return sanitize_config(raw)


def load_runtime_env() -> RuntimeEnv:
    return RuntimeEnv(
        inbox_path=os.getenv("INBOX_PATH", "/data/inbox"),
        state_db_path=os.getenv("STATE_DB_PATH", "/data/state/state.db"),
        webdav_mount_path=os.getenv("WEBDAV_MOUNT_PATH", "/mnt/webdav"),
        api_host=os.getenv("API_HOST", "0.0.0.0"),
        api_port=_env_int("API_PORT", 8080, minimum=1, maximum=65535),
        api_token=os.getenv("API_TOKEN", "").strip(),
        api_allow_no_token=_env_bool("API_ALLOW_NO_TOKEN", False),
        sync_max_workers=_env_int("SYNC_MAX_WORKERS", 1, minimum=1, maximum=4),
    )
