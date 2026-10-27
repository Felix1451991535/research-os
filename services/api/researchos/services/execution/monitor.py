"""Supervise one template process: streaming detectors (D01 non-finite, D05 stall), host-suspend detection
(D06), cancel (flag -> grace -> kill tree), lease loss, wall-time and memory limits. Guard lives OUTSIDE the
experiment process (baseline §7)."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

from ...contracts.execution import RunBundle
from ...contracts.tasks import TaskContext
from ..verification.detectors_run import streaming_nonfinite
from ..verification.view import read_jsonl
from .local_executor import Handle, LocalProcessExecutor, stderr_tail

SUSPEND_GAP_S = 60.0
POLL_S = 0.2


@dataclass
class Facts:
    exit_code: int | None = None
    cancelled: bool = False
    killed_reason: str | None = None
    stalled_idle_s: float | None = None
    suspend_gaps: list[float] = field(default_factory=list)
    stderr_tail: str = ""
    termination_failed: bool = False  # the process (or a descendant) outlived every kill attempt

    def as_extra(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "cancelled": self.cancelled,
            "stalled_idle_s": self.stalled_idle_s,
            "suspend_gaps": self.suspend_gaps,
            "stderr_tail": self.stderr_tail,
            "termination_failed": self.termination_failed,
        }


class Monitor:
    def __init__(
        self,
        ex: LocalProcessExecutor,
        h: Handle,
        bundle: RunBundle,
        ctx: TaskContext,
        grace_s: float,
        clock: Any = time,
    ) -> None:
        self.ex, self.h, self.b, self.ctx, self.grace, self.clock = ex, h, bundle, ctx, grace_s, clock
        self.f = Facts()
        lim = bundle.limits
        self.max_s = float(lim.get("max_minutes", 90)) * 60
        self.stall_s = float(lim.get("stall_s", 600))
        self.mem_mb = lim.get("memory_mb")

    def _kill(self, reason: str) -> None:
        self.f.killed_reason = self.f.killed_reason or reason
        if not self.ex.kill(self.h):
            self.f.termination_failed = True

    def _cancel(self) -> None:
        self.ex.request_cancel(self.h)
        end = self.clock.monotonic() + self.grace
        while self.clock.monotonic() < end and self.ex.poll(self.h).alive:
            self.clock.sleep(POLL_S)
        if self.ex.poll(self.h).alive:
            self._kill("cancelled")
        self.f.cancelled = True

    def _check(self, now: float, start: float, last_progress: float) -> bool:
        """Returns False when the run must stop."""
        if self.ctx.cancel_requested():
            self._cancel()
            return False
        if not self.ctx.check_lease():
            self._kill("lease_lost")
            return False
        if now - start > self.max_s:
            self._kill("time_limit")
            return False
        st = self.ex.poll(self.h)
        if self.mem_mb and st.rss_mb and st.rss_mb > float(self.mem_mb):
            self._kill("memory_limit")
            return False
        if now - last_progress > self.stall_s:
            self.f.stalled_idle_s = now - last_progress
            self._kill("stalled")
            return False
        return True

    def run(self) -> Facts:
        metrics = self.b.run_dir / "metrics.jsonl"
        offset, start = 0, self.clock.monotonic()
        last_progress, last_wall = start, self.clock.time()
        while self.ex.poll(self.h).alive:
            now, wall = self.clock.monotonic(), self.clock.time()
            if wall - last_wall > SUSPEND_GAP_S:  # host slept: do not count it as a stall
                self.f.suspend_gaps.append(wall - last_wall)
                last_progress = now
            last_wall = wall
            recs, offset = read_jsonl(metrics, offset)
            if recs:
                last_progress = now
                if streaming_nonfinite(recs):
                    self._kill("nonfinite")
                    break
                prog = [r for r in recs if r.get("kind") == "progress"]
                if prog:
                    self.ctx.progress(
                        str(prog[-1].get("stage") or "running"),
                        {"step": prog[-1].get("step"), "total": prog[-1].get("total")},
                    )
            if not self._check(now, start, last_progress):
                break
            self.clock.sleep(POLL_S)
        try:
            self.f.exit_code = self.h.child.popen.wait(timeout=30)
        except subprocess.TimeoutExpired:  # review #24: never let a lingering process crash the handler
            self.f.termination_failed = True
        self.ex.close(self.h)
        self.f.stderr_tail = stderr_tail(self.h)
        return self.f
