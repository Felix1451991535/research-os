"""Local-process executor (CPU default; GPU templates run here too with an honest device label).
Separate process + new group/Job Object; kill tree on cancel/timeout; RSS watchdog via psutil."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import psutil

from ...contracts.execution import ExecStatus, RunBundle
from ...platform.process import ChildProcess, kill_own_child, kill_tree, spawn

KILL_WAIT_S = 10.0


@dataclass
class Handle:
    child: ChildProcess
    out: IO[bytes]
    err: IO[bytes]


class LocalProcessExecutor:
    name = "local"

    def start(self, bundle: RunBundle) -> Handle:
        out = (bundle.run_dir / "stdout.log").open("wb")
        err = (bundle.run_dir / "stderr.log").open("wb")
        child = spawn(
            [sys.executable, "-u", str(bundle.entry)],
            cwd=bundle.run_dir,
            env=bundle.env,
            stdout=out,
            stderr=err,
            memory_limit_mb=bundle.limits.get("memory_mb"),
        )
        return Handle(child, out, err)

    def poll(self, h: Handle) -> ExecStatus:
        code = h.child.popen.poll()
        rss = None
        if code is None:
            try:
                p = psutil.Process(h.child.pid)
                rss = sum(x.memory_info().rss for x in [p, *p.children(recursive=True)]) / 1e6
            except psutil.Error:
                pass
        return ExecStatus(alive=code is None, exit_code=code, rss_mb=rss)

    def request_cancel(self, h: Handle) -> None:
        """Graceful stop: the SDK polls this flag file; the monitor hard-kills after the grace period."""
        (self.run_dir_of(h) / "CANCEL").touch()

    @staticmethod
    def run_dir_of(h: Handle) -> Path:
        return Path(h.out.name).parent

    def kill(self, h: Handle) -> bool:
        ok = kill_tree(h.child.pid, h.child.create_time, h.child.job, grace_s=0.5)
        try:
            h.child.popen.wait(timeout=KILL_WAIT_S)
            return ok
        except subprocess.TimeoutExpired:  # seen under load: the tree kill did not take; never crash the handler
            return kill_own_child(h.child, KILL_WAIT_S)  # verified final outcome (review #25)

    def close(self, h: Handle) -> None:
        h.out.close()
        h.err.close()


def stderr_tail(h: Handle, n: int = 2000) -> str:
    try:
        return Path(h.err.name).read_bytes()[-n:].decode("utf-8", errors="replace")
    except OSError:
        return ""
