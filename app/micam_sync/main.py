from __future__ import annotations

import logging
import os
import signal
import threading

from waitress import serve

from micam_sync.api import create_app
from micam_sync.config import load_runtime_env
from micam_sync.db import StateDB
from micam_sync.scanner import InboxScanner
from micam_sync.sync_worker import SyncWorker


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def main() -> None:
    configure_logging()
    log = logging.getLogger("micam_sync.main")
    runtime = load_runtime_env()

    os.makedirs(runtime.inbox_path, exist_ok=True)
    os.makedirs(os.path.dirname(runtime.state_db_path), exist_ok=True)
    os.makedirs(runtime.webdav_mount_path, exist_ok=True)

    db = StateDB(runtime.state_db_path)
    db.reset_stale_syncing()

    stop_event = threading.Event()
    scanner = InboxScanner(runtime, db, stop_event)
    scanner.start()

    workers = []
    for idx in range(runtime.sync_max_workers):
        worker = SyncWorker(runtime, db, stop_event, worker_name=f"sync-{idx + 1}")
        worker.start()
        workers.append(worker)

    app = create_app(runtime, db)

    def shutdown_handler(signum: int, _frame: object) -> None:
        log.info("received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    log.info("API listening on %s:%s", runtime.api_host, runtime.api_port)
    serve(app, host=runtime.api_host, port=runtime.api_port, threads=8)

    stop_event.set()
    scanner.join(timeout=5)
    for worker in workers:
        worker.join(timeout=5)


if __name__ == "__main__":
    main()
