"""M2 gates: worker crash mid-experiment keeps records and the orphaned template process is killed by the next
worker's reaper (no rerun); host suspend is detected by the monitor (D06), not counted as a stall."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from researchos.kernel.config import Settings

from .conftest import Client
from .fixtures.make_dataset import make
from .m2_helpers import new_plan, plan_content

REPO = Path(__file__).resolve().parents[1]


def _worker(settings: Settings) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "RESEARCHOS_DATA_DIR": str(settings.data_dir),
        "RESEARCHOS_EXTRA_TEMPLATE_DIRS": ",".join(settings.extra_template_dirs),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "researchos_worker.main"],
        env=env,
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _state(client: Client, tid: str, want: set[str], timeout: float = 30) -> str:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = client.get(f"/api/v1/tasks/{tid}").json()["state"]
        if st in want:
            return st
        time.sleep(0.2)
    raise AssertionError(f"stuck in {st}")


def test_worker_crash_kills_orphan_and_keeps_records(
    client: Client, conn: sqlite3.Connection, settings: Settings, tmp_path: Path
) -> None:
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds, params={"mode": "slow"}, limits={"max_minutes": 2, "stall_s": 60}))
    tid = client.post(
        f"/api/v1/plans/{pid}/revisions/1/runs", json={"stage": "trial", "params": {"mode": "slow"}}
    ).json()["id"]
    w1 = _worker(settings)
    try:
        _state(client, tid, {"running"})
        child: Any = None
        for _ in range(50):
            child = conn.execute("SELECT pid, pid_create_time FROM runs WHERE task_id=?", (tid,)).fetchone()
            if child and child[0]:
                break
            time.sleep(0.1)
        assert child and psutil.pid_exists(child[0])
    finally:
        w1.send_signal(signal.SIGKILL)
        w1.wait(timeout=5)
    assert psutil.pid_exists(child[0])  # orphan still running after the worker died
    time.sleep(1.5)
    w2 = _worker(settings)
    try:
        assert _state(client, tid, {"interrupted", "blocked", "failed", "succeeded"}) == "interrupted"
    finally:
        w2.send_signal(signal.SIGTERM)
        w2.wait(timeout=20)
    time.sleep(0.5)
    p = psutil.Process(child[0]) if psutil.pid_exists(child[0]) else None
    assert p is None or p.status() == psutil.STATUS_ZOMBIE
    run = conn.execute("SELECT state, run_dir FROM runs WHERE task_id=?", (tid,)).fetchone()
    assert run[0] == "lost" and (settings.data_dir / run[1] / "run.json").is_file()  # records kept


class FakeClock:
    def __init__(self) -> None:
        self.mono, self.wall = 0.0, 1_000_000.0

    def monotonic(self) -> float:
        return self.mono

    def time(self) -> float:
        return self.wall

    def sleep(self, s: float) -> None:
        time.sleep(0.01)  # keep real polling cheap; fake time advances by the requested amount
        self.mono += s
        self.wall += s
        if 1.0 <= self.mono < 1.0 + s:  # laptop sleeps for 30 minutes once
            self.wall += 1800


def test_monitor_detects_suspend_not_stall(tmp_path: Path) -> None:
    from researchos.contracts.execution import RunBundle
    from researchos.platform.env import child_env
    from researchos.services.execution.local_executor import LocalProcessExecutor
    from researchos.services.execution.monitor import Monitor

    run_dir = tmp_path / "r"
    run_dir.mkdir()
    entry = tmp_path / "t.py"
    entry.write_text("import time\ntime.sleep(2)\n")
    b = RunBundle("r1", run_dir, entry, child_env(tmp_path / "h"), {"stall_s": 600}, "cpu:test")

    class Ctx:
        task_id, run_id, payload = "t", "r1", {}

        def progress(self, *a: Any) -> None: ...
        def cancel_requested(self) -> bool:
            return False

        def check_lease(self) -> bool:
            return True

    ex = LocalProcessExecutor()
    f = Monitor(ex, ex.start(b), b, Ctx(), 1.0, clock=FakeClock()).run()  # type: ignore[arg-type]
    assert f.suspend_gaps and f.suspend_gaps[0] >= 1800 and f.stalled_idle_s is None and f.exit_code == 0


def test_backup_verifies_run_artifacts(client: Client, conn: sqlite3.Connection, settings: Settings,
                                       tmp_path: Path) -> None:
    from researchos.kernel.backup import create_backup, restore_backup
    from researchos.services.wiring import build_services

    from .m2_helpers import run
    svc = build_services(settings, load_plugins=False, providers={})
    pid = new_plan(client, plan_content(client, None, template="smoke_cpu", params={"n": 50, "steps": 1}))
    assert run(client, conn, svc, pid, stage="smoke")["state"] == "succeeded"
    b = create_backup(settings, conn, "m2")
    res = restore_backup(Path(b["path"]), tmp_path / "restored")
    assert res["ok"], res["problems"]
    assert conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] > 0


def _dead(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _spawn_with_descendant(tmp_path: Path, launcher: str) -> tuple[Any, int, Any, Any]:
    from researchos.platform.process import spawn

    marker = tmp_path / "grandchild"
    out, err = (tmp_path / "out").open("wb"), (tmp_path / "err").open("wb")
    child = spawn(["sh", "-c", f"{launcher} sleep 60 & echo $! > {marker}.tmp && mv {marker}.tmp {marker}; wait"],
                  cwd=tmp_path, env={"PATH": os.environ["PATH"]}, stdout=out, stderr=err)
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    return child, int(marker.read_text()), out, err


@pytest.mark.parametrize("launcher", ["", "setsid"])
def test_kill_falls_back_to_own_child_when_tree_kill_misses(tmp_path: Path, monkeypatch: Any, launcher: str) -> None:
    """Regressions: kill_tree did not take under load and popen.wait's TimeoutExpired crashed the run handler
    (ADR-145); the fallback then missed a descendant that left the process group via setsid yet reported success
    (review #25). The fallback kills every current descendant and returns the verified outcome."""
    from researchos.services.execution import local_executor

    monkeypatch.setattr(local_executor, "kill_tree", lambda *a, **k: True)  # the tree kill silently misses
    monkeypatch.setattr(local_executor, "KILL_WAIT_S", 0.5)
    child, grandchild, out, err = _spawn_with_descendant(tmp_path, launcher)
    with out, err:
        assert local_executor.LocalProcessExecutor().kill(local_executor.Handle(child, out, err)) is True
    assert child.popen.returncode == -signal.SIGKILL
    deadline = time.monotonic() + 5
    while not _dead(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _dead(grandchild)


def test_fallback_reports_a_surviving_descendant(tmp_path: Path, monkeypatch: Any) -> None:
    from researchos.platform import process

    child, grandchild, out, err = _spawn_with_descendant(tmp_path, "setsid")
    real_kill = psutil.Process.kill
    monkeypatch.setattr(psutil.Process, "kill", lambda self: None)  # the descendant ignores the kill
    try:
        with out, err:
            assert process.kill_own_child(child, 0.3) is False
    finally:
        monkeypatch.setattr(psutil.Process, "kill", real_kill)
        os.kill(grandchild, signal.SIGKILL)


@pytest.mark.parametrize("inspection", ["children", "status"])
def test_fallback_inspection_denied_fails_monitor(tmp_path: Path, monkeypatch: Any, inspection: str) -> None:
    """Review #36: an uninspectable live descendant must not become verified termination."""
    from types import SimpleNamespace

    from researchos.contracts.execution import RunBundle
    from researchos.services.execution import local_executor
    from researchos.services.execution.monitor import Monitor
    from researchos.services.execution.run_task import _outcome

    child, grandchild, out, err = _spawn_with_descendant(tmp_path, "setsid")
    original = getattr(psutil.Process, inspection)
    real_kill = psutil.Process.kill

    def inspect(p: psutil.Process, *args: Any, **kwargs: Any) -> Any:
        if p.pid == (child.pid if inspection == "children" else grandchild):
            raise psutil.AccessDenied(p.pid)
        return original(p, *args, **kwargs)

    def kill(p: psutil.Process) -> None:
        if p.pid == grandchild:
            raise psutil.AccessDenied(p.pid)
        real_kill(p)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(local_executor, "kill_tree", lambda *a, **k: True)
            patch.setattr(local_executor, "KILL_WAIT_S", 0.05)
            patch.setattr(psutil.Process, inspection, inspect)
            patch.setattr(psutil.Process, "kill", kill)
            ctx = SimpleNamespace(cancel_requested=lambda: False, check_lease=lambda: False)
            bundle = RunBundle("r1", tmp_path, tmp_path / "t.py", {}, {}, "cpu:test")
            facts = Monitor(local_executor.LocalProcessExecutor(), local_executor.Handle(child, out, err),
                            bundle, ctx, 0.0).run()
        assert child.popen.returncode == -signal.SIGKILL
        assert not _dead(grandchild)
        assert facts.termination_failed and facts.killed_reason == "lease_lost"
        outcome = _outcome(facts, [], [], "r1")
        assert outcome.state.value == "failed" and outcome.error_code == "termination_failed"
        assert "lease_lost" in outcome.error_message
    finally:
        os.kill(grandchild, signal.SIGKILL)
        child.popen.kill()
        child.popen.wait(timeout=5)
        out.close()
        err.close()


def test_monitor_survives_a_failed_termination(tmp_path: Path) -> None:
    """Regression (review #24): kill() -> False was ignored and the final unguarded wait raised."""
    from types import SimpleNamespace

    from researchos.contracts.execution import ExecStatus, RunBundle
    from researchos.services.execution.monitor import Monitor
    from researchos.services.execution.run_task import _outcome

    class Popen:
        def wait(self, timeout: float) -> int:
            raise subprocess.TimeoutExpired("t", timeout)

    class Ex:
        def poll(self, h: Any) -> ExecStatus:
            return ExecStatus(alive=True, exit_code=None, rss_mb=None)

        def request_cancel(self, h: Any) -> None: ...
        def kill(self, h: Any) -> bool:
            return False

        def close(self, h: Any) -> None: ...

    class Ctx:
        task_id, run_id, payload = "t", "r1", {}

        def progress(self, *a: Any) -> None: ...
        def cancel_requested(self) -> bool:
            return True

        def check_lease(self) -> bool:
            return True

    (tmp_path / "err").write_bytes(b"")
    h = SimpleNamespace(child=SimpleNamespace(popen=Popen()), err=SimpleNamespace(name=str(tmp_path / "err")))
    b = RunBundle("r1", tmp_path, tmp_path / "t.py", {}, {}, "cpu:test")
    f = Monitor(Ex(), h, b, Ctx(), 0.0, clock=FakeClock()).run()  # type: ignore[arg-type]
    assert f.termination_failed and f.killed_reason == "cancelled" and f.exit_code is None
    out = _outcome(f, [], [], "r1")
    assert out.state.value == "failed" and out.error_code == "termination_failed" and "cancelled" in out.error_message
