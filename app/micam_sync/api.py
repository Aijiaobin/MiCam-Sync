from __future__ import annotations

import base64
import os
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from flask import Flask, abort, jsonify, render_template, request

from micam_sync.config import ALLOWED_FILE_STATES, RuntimeEnv
from micam_sync.db import StateDB


SMB_PROTOCOL_ORDER = (
    "CORE",
    "COREPLUS",
    "LANMAN1",
    "LANMAN2",
    "NT1",
    "SMB2",
    "SMB2_02",
    "SMB2_10",
    "SMB3",
    "SMB3_00",
    "SMB3_02",
    "SMB3_11",
)


def _extract_token() -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1].strip()
    return request.headers.get("X-API-Token", "").strip()


def _smb_test_result(payload: dict[str, Any], runtime: RuntimeEnv) -> tuple[bool, str]:
    user = str(payload.get("smb_user", "")).strip()
    password = str(payload.get("smb_password", ""))
    share_name = str(payload.get("smb_share_name", "")).strip()
    min_proto = str(payload.get("smb_min_protocol", "")).strip()
    max_proto = str(payload.get("smb_max_protocol", "")).strip()
    lanman = str(payload.get("smb_enable_lanman", "")).strip().lower()

    if not user or not share_name:
        return False, "SMB user/share name are required"
    if not password:
        password = os.getenv("SMB_PASSWORD", "")
    if not password:
        return False, "SMB password is required (or provide container default SMB_PASSWORD)"
    if not min_proto or not max_proto:
        return False, "SMB min/max protocol are required"
    if min_proto not in SMB_PROTOCOL_ORDER or max_proto not in SMB_PROTOCOL_ORDER:
        return False, "Unsupported SMB protocol"
    if SMB_PROTOCOL_ORDER.index(min_proto) > SMB_PROTOCOL_ORDER.index(max_proto):
        return False, "SMB min protocol must be <= max protocol"
    if lanman not in {"yes", "no"}:
        return False, "SMB enable_lanman must be yes/no"
    if not os.path.isdir(runtime.inbox_path):
        return False, f"Inbox path does not exist: {runtime.inbox_path}"
    if not os.access(runtime.inbox_path, os.W_OK):
        return False, f"Inbox path is not writable: {runtime.inbox_path}"
    return True, "SMB settings are valid and inbox path is writable"


def _webdav_test_result(payload: dict[str, Any]) -> tuple[bool, str, int | None]:
    url = str(payload.get("webdav_url", "")).strip()
    user = str(payload.get("webdav_user", "")).strip()
    password = str(payload.get("webdav_pass", ""))
    remote_path = str(payload.get("webdav_remote_path", "/")).strip() or "/"

    if not url or not user or not password:
        return False, "WebDAV URL/user/password are required", None
    if not (url.startswith("http://") or url.startswith("https://")):
        return False, "WebDAV URL must start with http:// or https://", None

    auth_token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")

    def _send(method: str, target_url: str) -> tuple[int, dict[str, str]]:
        req = urlrequest.Request(url=target_url, method=method)
        req.add_header("Authorization", f"Basic {auth_token}")
        if method == "PROPFIND":
            req.add_header("Depth", "0")
        try:
            with urlrequest.urlopen(req, timeout=8) as resp:
                headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
                return int(resp.status), headers
        except urlerror.HTTPError as exc:
            headers = {str(k).lower(): str(v) for k, v in exc.headers.items()}
            return int(exc.code), headers

    try:
        options_code, options_headers = _send("OPTIONS", url)
        probe_url = url.rstrip("/") + "/" + remote_path.strip("/") if remote_path.strip("/") else url
        propfind_code, propfind_headers = _send("PROPFIND", probe_url)
    except Exception as exc:  # noqa: BLE001
        return False, f"WebDAV request failed: {exc}", None

    options_allow = options_headers.get("allow", "")
    options_dav = options_headers.get("dav", "")
    propfind_allow = propfind_headers.get("allow", "")

    options_ok = options_code in {200, 204, 207, 401, 403} or (
        options_code == 405 and ("propfind" in options_allow.lower() or bool(options_dav.strip()))
    )
    propfind_ok = propfind_code in {200, 207, 301, 302, 401, 403}

    if options_ok and propfind_ok:
        return (
            True,
            f"WebDAV protocol check passed (OPTIONS {options_code}, PROPFIND {propfind_code})",
            propfind_code,
        )

    if propfind_code in {405, 501}:
        hint = ""
        if not options_dav.strip() and "propfind" not in options_allow.lower() and "propfind" not in propfind_allow.lower():
            hint = " endpoint does not advertise DAV/PROPFIND"
        return (
            False,
            f"WebDAV protocol check failed: server rejected PROPFIND ({propfind_code}) at {probe_url};"
            f" likely not a WebDAV endpoint or method is disabled.{hint}",
            propfind_code,
        )

    if propfind_code == 404:
        return (
            False,
            f"WebDAV protocol check failed: remote path not found ({probe_url}, PROPFIND 404)",
            propfind_code,
        )

    return (
        False,
        f"WebDAV protocol check failed (OPTIONS {options_code}, PROPFIND {propfind_code})",
        propfind_code,
    )


def create_app(runtime: RuntimeEnv, db: StateDB) -> Flask:
    app = Flask(__name__, template_folder="templates")

    @app.before_request
    def require_auth() -> None:
        if not request.path.startswith("/api"):
            return
        if request.path == "/api/health":
            return

        if runtime.api_allow_no_token:
            return

        if not runtime.api_token:
            abort(503, description="api token is required but not configured")

        if _extract_token() != runtime.api_token:
            abort(401, description="unauthorized")

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/api/health")
    def health() -> Any:
        return jsonify({"status": "ok", "mount_ready": db.is_mount_ready(runtime.webdav_mount_path)})

    @app.get("/api/setup")
    def setup() -> Any:
        return jsonify(
            {
                "auth": {"token_required": not runtime.api_allow_no_token and bool(runtime.api_token)},
                "smb": {
                    "smb_user": os.getenv("SMB_USER", "micam"),
                    "smb_password_configured": bool(os.getenv("SMB_PASSWORD", "")),
                    "smb_workgroup": os.getenv("SMB_WORKGROUP", "WORKGROUP"),
                    "smb_share_name": os.getenv("SMB_SHARE_NAME", "MI_CAMERA"),
                    "smb_min_protocol": os.getenv("SMB_MIN_PROTOCOL", "NT1"),
                    "smb_max_protocol": os.getenv("SMB_MAX_PROTOCOL", "SMB3"),
                    "smb_enable_lanman": os.getenv("SMB_ENABLE_LANMAN", "no"),
                },
                "webdav": {
                    "webdav_url": os.getenv("WEBDAV_URL", ""),
                    "webdav_user": os.getenv("WEBDAV_USER", ""),
                    "webdav_remote_path": os.getenv("WEBDAV_REMOTE_PATH", "/"),
                    "webdav_mount_path": runtime.webdav_mount_path,
                },
            }
        )

    @app.post("/api/test/smb")
    def test_smb() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400, description="invalid JSON body")
        ok, detail = _smb_test_result(body, runtime)
        return jsonify({"ok": ok, "detail": detail})

    @app.post("/api/test/webdav")
    def test_webdav() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400, description="invalid JSON body")
        ok, detail, status_code = _webdav_test_result(body)
        return jsonify({"ok": ok, "detail": detail, "http_status": status_code})

    @app.post("/api/test/mount")
    def test_mount() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400, description="invalid JSON body")

        mount_path = str(body.get("webdav_mount_path", runtime.webdav_mount_path)).strip() or runtime.webdav_mount_path
        if not os.path.isabs(mount_path):
            abort(400, description="webdav_mount_path must be absolute path")

        mount_exists = os.path.isdir(mount_path)
        mount_ready = db.is_mount_ready(mount_path)
        write_ready = mount_exists and os.access(mount_path, os.W_OK)
        ok = mount_exists and mount_ready and write_ready
        if ok:
            detail = f"mount is ready at {mount_path}"
        else:
            detail = f"mount is not ready at {mount_path}"

        return jsonify(
            {
                "ok": ok,
                "detail": detail,
                "mount_path": mount_path,
                "mount_exists": mount_exists,
                "mount_ready": mount_ready,
                "write_ready": write_ready,
            }
        )

    @app.post("/api/test/sync")
    def test_sync() -> Any:
        cfg = db.get_config()
        mount_ready = db.is_mount_ready(runtime.webdav_mount_path)
        write_ready = os.path.isdir(runtime.webdav_mount_path) and os.access(runtime.webdav_mount_path, os.W_OK)
        ok = mount_ready and write_ready
        detail = "sync destination is ready" if ok else "sync destination is not ready"
        return jsonify(
            {
                "ok": ok,
                "detail": detail,
                "mount_ready": mount_ready,
                "write_ready": write_ready,
                "target_subdir": cfg.get("target_subdir", ""),
                "queue": db.queue_stats(),
            }
        )

    @app.get("/api/config")
    def get_config() -> Any:
        payload: dict[str, Any] = dict(db.get_config())
        payload["requires_restart_for"] = ["WEBDAV_*", "SMB_*"]
        return jsonify(payload)

    @app.put("/api/config")
    def set_config() -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400, description="invalid JSON body")

        allowed = {
            "sync_delay_seconds",
            "scan_interval_seconds",
            "target_subdir",
            "delete_after_sync",
            "sync_retry_base_seconds",
            "sync_retry_max_seconds",
            "sync_copy_chunk_bytes",
            "setup_completed",
        }
        updates = {k: body[k] for k in body.keys() if k in allowed}
        if not updates:
            abort(400, description="no mutable settings provided")

        try:
            updated = db.update_config(updates)
        except (TypeError, ValueError) as exc:
            abort(400, description=f"invalid config: {exc}")
        return jsonify({"ok": True, "config": updated})

    @app.get("/api/queue")
    def queue_summary() -> Any:
        return jsonify(db.queue_stats())

    @app.get("/api/files")
    def queue_files() -> Any:
        raw_limit = request.args.get("limit", "100")
        state = request.args.get("state")
        try:
            limit = min(max(int(raw_limit), 1), 500)
        except ValueError:
            abort(400, description="limit must be int")

        if state is not None and state not in ALLOWED_FILE_STATES:
            abort(400, description=f"state must be one of: {', '.join(sorted(ALLOWED_FILE_STATES))}")
        return jsonify(db.list_files(limit=limit, state=state))

    @app.errorhandler(400)
    def bad_request(err: Exception) -> Any:
        return jsonify({"error": str(err)}), 400

    @app.errorhandler(401)
    def unauthorized(err: Exception) -> Any:
        return jsonify({"error": str(err)}), 401

    @app.errorhandler(503)
    def unavailable(err: Exception) -> Any:
        return jsonify({"error": str(err)}), 503

    return app
