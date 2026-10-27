"""M0 gate: a REAL worker process killed mid-run -> task interrupted (not rerun); new worker keeps working;
worker refuses to start with pending migrations."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from researchos.kernel.config import Settings
from researchos.kernel.db import connect

from .conftest import Client

REPO = Path(__file__).resolve().parents[1]


def _worker(settings: Settings, *args: str) -> subprocess.Popen[bytes]:
    env = {**os.environ, "RESEARCHOS_DATA_DIR": str(settings.data_dir)}
    return subprocess.Popen([sys.executable, "-m", "researchos_worker.main", *args], env=env, cwd=REPO,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _wait_state(client: Client, tid: str, states: set[str], timeout: float = 20) -> str:
    end = time.monotonic() + timeout
    st = ""
    while time.monotonic() < end:
        st = client.get(f"/api/v1/tasks/{tid}").json()["state"]
        if st in states:
            return st
        time.sleep(0.1)
    raise AssertionError(f"task {tid} stuck in {st}")


def test_kill_worker_mid_run_then_recover(client: Client, settings: Settings) -> None:
    tid = client.post("/api/v1/tasks", json={"kind": "system.sleep", "payload": {"seconds": 30}}).json()["id"]
    w1 = _worker(settings)
    try:
        _wait_state(client, tid, {"running"})
    finally:
        w1.send_signal(signal.SIGKILL)
        w1.wait(timeout=5)
    quick = client.post("/api/v1/tasks", json={"kind": "system.sleep", "payload": {"seconds": 0.1}}).json()["id"]
    time.sleep(1.5)  # lease (1 s in test config) + worker heartbeat go stale
    w2 = _worker(settings)
    try:
        assert _wait_state(client, tid, {"interrupted", "succeeded", "failed"}) == "interrupted"
        assert _wait_state(client, quick, {"succeeded", "failed"}) == "succeeded"
    finally:
        w2.send_signal(signal.SIGTERM)
        w2.wait(timeout=10)
    t = client.get(f"/api/v1/tasks/{tid}").json()
    assert t["attempt"] == 1 and t["error_code"] is None  # never silently rerun
    conn = connect(settings.db_path)
    assert conn.execute("SELECT state FROM runs WHERE task_id=?", (tid,)).fetchone()[0] == "lost"
    assert conn.execute("SELECT stopped_at IS NOT NULL FROM workers ORDER BY started_at DESC LIMIT 1").fetchone()[0]
    conn.close()


def test_worker_cancels_running_task(client: Client, settings: Settings) -> None:
    tid = client.post("/api/v1/tasks", json={"kind": "system.sleep", "payload": {"seconds": 30}}).json()["id"]
    w = _worker(settings)
    try:
        _wait_state(client, tid, {"running"})
        client.post(f"/api/v1/tasks/{tid}/cancel")
        assert _wait_state(client, tid, {"cancelled", "failed", "succeeded"}) == "cancelled"
    finally:
        w.send_signal(signal.SIGTERM)
        w.wait(timeout=10)


def test_worker_refuses_pending_migrations(client: Client, settings: Settings) -> None:
    conn = connect(settings.db_path)
    conn.execute("DELETE FROM schema_migrations WHERE namespace='core'")
    conn.close()
    w = _worker(settings, "--once")
    _, err = w.communicate(timeout=60)  # reads stderr while waiting (a full pipe would block the worker)
    assert w.returncode == 3
    assert b"pending migrations" in (err or b"")
