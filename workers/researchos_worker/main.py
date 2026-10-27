"""`researchos-worker`: separate process that executes tasks. Refuses to start on pending migrations."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from researchos import __version__
from researchos.kernel.config import load_settings
from researchos.kernel.db import connect
from researchos.kernel.logsetup import setup_logging
from researchos.kernel.migrate import PendingMigrations, require_current
from researchos.services.hardening import harden_process
from researchos.services.orchestration import workers
from researchos.services.wiring import build_services

from .loop import WorkerState, run_loop

log = logging.getLogger("researchos.worker")


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="researchos-worker")
    ap.add_argument("--data-dir")
    ap.add_argument("--poll", type=float, default=0.5)
    ap.add_argument("--once", action="store_true", help="process at most one task, then exit")
    a = ap.parse_args(argv)
    settings = load_settings(a.data_dir)
    setup_logging(settings.logs_dir, "worker")
    svc = build_services(settings)
    harden_process(svc)  # provider keys captured in memory, inherited env scrubbed
    if not settings.db_path.is_file():
        print("database not initialized; start the API first", file=sys.stderr)
        return 3
    conn = connect(settings.db_path)
    try:
        require_current(conn, svc.migration_sources)
    except PendingMigrations as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    st = WorkerState(workers.register(conn, os.getpid(), __version__))

    def _stop(signum: int, frame: object) -> None:
        log.info("signal %s: stopping after current task", signum)
        st.stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        run_loop(conn, svc, st, a.poll, a.once)
    finally:
        workers.stop(conn, st.worker_id)
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(run())
