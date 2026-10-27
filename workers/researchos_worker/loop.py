"""Worker main loop: heartbeat, suspend detection, reaping, claim -> run handler with lease renewal -> finish."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from researchos.contracts.tasks import TaskOutcome, TaskState
from researchos.kernel.db import jload
from researchos.kernel.events import emit
from researchos.platform.sysinfo import inhibit_sleep
from researchos.services.container import Services
from researchos.services.execution import study
from researchos.services.orchestration import leases, workers
from researchos.services.orchestration.triggers import run_triggers

from .context import WorkerContext

log = logging.getLogger("researchos.worker")
SUSPEND_GAP_S = 60.0


@dataclass
class WorkerState:
    worker_id: str
    stop: threading.Event = field(default_factory=threading.Event)
    last_wall: float = field(default_factory=time.time)
    last_trigger: float = field(default_factory=time.monotonic)  # first tick one interval after start


def detect_suspend(conn: sqlite3.Connection, st: WorkerState, now: float | None = None) -> float | None:
    """Loop iterations are seconds apart; a large wall-clock gap means the host slept (or the process froze)."""
    now = time.time() if now is None else now
    gap = now - st.last_wall
    st.last_wall = now
    if gap > SUSPEND_GAP_S:
        emit(conn, "host.suspend_detected", actor=f"worker:{st.worker_id}", entity_type="worker",
             entity_id=st.worker_id, data={"gap_s": round(gap, 1)})
        return gap
    return None


def _run_handler(svc: Services, claim: leases.Claim, ctx: WorkerContext, out: list[TaskOutcome]) -> None:
    spec = svc.kinds.get(claim.task["kind"])
    try:
        if spec is None or spec.handler is None:
            out.append(TaskOutcome(TaskState.FAILED, error_code="no_handler"))
            return
        out.append(spec.handler(ctx))
    except Exception as e:  # handler bugs become recorded failures, never silent
        log.exception("handler crashed task=%s", claim.task["id"])
        out.append(TaskOutcome(TaskState.FAILED, error_code="handler_exception", error_message=str(e)[:500]))


def execute(conn: sqlite3.Connection, svc: Services, st: WorkerState, claim: leases.Claim) -> bool:
    """Run one claimed task to completion. Returns False if the result was dropped (fenced)."""
    s = svc.settings
    ctx = WorkerContext(claim.task["id"], jload(claim.task["payload"]) or {}, claim.run_id)
    out: list[TaskOutcome] = []
    th = threading.Thread(target=_run_handler, args=(svc, claim, ctx, out), daemon=True)
    inhibit_sleep(True)
    th.start()
    try:
        while th.is_alive():
            th.join(timeout=s.heartbeat_s)
            if not th.is_alive():
                break
            stage, prog = ctx.take_progress()
            status = leases.renew(conn, claim.task["id"], st.worker_id, claim.generation, s.lease_s, stage, prog)
            workers.heartbeat(conn, st.worker_id)
            if status == "cancel":
                ctx.request_cancel()
            elif status == "fenced":
                ctx.mark_lost()
                log.warning("lease lost for task %s; result will be dropped", claim.task["id"])
            if st.stop.is_set():
                ctx.request_cancel()
    finally:
        inhibit_sleep(False)
    outcome = out[0] if out else TaskOutcome(TaskState.FAILED, error_code="no_outcome")
    done = leases.finish(conn, claim, st.worker_id, outcome)
    study_id = (ctx.payload or {}).get("study_id")
    if done and study_id:  # event-driven protocol: move the study on when its stage's runs are all finished
        try:
            study.advance(conn, svc.kinds, str(study_id), f"worker:{st.worker_id}")
        except Exception:
            log.exception("study advance failed for %s", study_id)
    return done


def run_loop(conn: sqlite3.Connection, svc: Services, st: WorkerState, poll_s: float = 0.5,
             once: bool = False) -> int:
    """Process tasks until stopped. With once=True: process at most one task (tests)."""
    done = 0
    last_reap = 0.0
    while not st.stop.is_set():
        detect_suspend(conn, st)
        workers.heartbeat(conn, st.worker_id)
        if time.monotonic() - last_reap > svc.settings.heartbeat_s:
            for tid in leases.reap(conn, st.worker_id, svc.kinds, svc.settings.lease_s):
                log.info("reaped task %s", tid)
            last_reap = time.monotonic()
        if svc.triggers and time.monotonic() - st.last_trigger > svc.settings.trigger_interval_s:
            run_triggers(conn, svc.kinds, svc.triggers, datetime.now(UTC))
            st.last_trigger = time.monotonic()
        claim = leases.claim(conn, st.worker_id, svc.settings.lease_s, svc.kinds)
        if claim is None:
            if once:
                return done
            st.stop.wait(poll_s)
            continue
        execute(conn, svc, st, claim)
        done += 1
        if once:
            return done
    return done
