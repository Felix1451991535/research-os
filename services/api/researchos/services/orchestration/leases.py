"""Worker-side K2 operations: claim with expiring lease, renew, finish (generation-fenced), reap dead leases.

At-least-once claim; a stale worker (lease lost) is fenced by the monotonic lease_generation and its results are
dropped. Reaping runs only inside a worker and skips the worker's own leases, so a host that wakes from sleep lets
its worker renew first instead of having healthy tasks interrupted by someone else.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from ...contracts.tasks import ResourceClass, RetryPolicy, TaskOutcome, TaskState
from ...kernel.db import Row, jdump, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from ...platform.process import kill_tree, pid_alive
from .registry import KindRegistry
from .transitions import load, transition

SERIAL_CLASSES = (ResourceClass.GPU.value, ResourceClass.LLM.value)


@dataclass(frozen=True)
class Claim:
    task: Row
    run_id: str
    generation: int


def _iso_in(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")


def claim(conn: sqlite3.Connection, worker_id: str, lease_s: float, kinds: KindRegistry) -> Claim | None:
    names = [k.name for k in kinds.all() if k.handler is not None]
    if not names:
        return None
    with tx(conn):
        busy = {r[0] for r in conn.execute(
            "SELECT DISTINCT resource_class FROM tasks WHERE state IN ('running','cancelling') "
            "AND resource_class IN (?,?)",
            SERIAL_CLASSES)}
        marks = ",".join("?" * len(names))
        excl = ",".join("?" * len(busy)) if busy else "''"
        cand = row(conn.execute(
            f"SELECT * FROM tasks WHERE state='queued' AND kind IN ({marks}) AND resource_class NOT IN ({excl}) "
            "AND (not_before IS NULL OR not_before<=?) ORDER BY priority DESC, created_at LIMIT 1",
            (*names, *busy, utcnow())))
        if not cand:
            return None
        gen = int(cand["lease_generation"]) + 1
        attempt = int(cand["attempt"]) + 1
        transition(conn, cand, TaskState.RUNNING, actor=f"worker:{worker_id}", reason="claimed",
                   fields={"lease_owner": worker_id, "lease_expires_at": _iso_in(lease_s),
                           "lease_generation": gen, "attempt": attempt})
        run_id = new_id("run")
        conn.execute("INSERT INTO runs(id,task_id,attempt,lease_generation,worker_id,trace_id,state,started_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (run_id, cand["id"], attempt, gen, worker_id, cand["trace_id"],
                                                 "running", utcnow()))
        return Claim(load(conn, cand["id"]), run_id, gen)


RenewStatus = Literal["ok", "cancel", "fenced"]


def renew(conn: sqlite3.Connection, task_id: str, worker_id: str, generation: int, lease_s: float,
          stage: str | None = None, progress: dict[str, object] | None = None) -> RenewStatus:
    sets, args = "lease_expires_at=?, updated_at=?", [_iso_in(lease_s), utcnow()]
    if stage is not None:
        sets += ", stage=?"
        args.append(stage)
    if progress is not None:
        sets += ", progress=?"
        args.append(jdump(progress))
    with tx(conn):
        cur = conn.execute(f"UPDATE tasks SET {sets} WHERE id=? AND lease_owner=? AND lease_generation=? "
                           "AND state IN ('running','cancelling')", (*args, task_id, worker_id, generation))
        if cur.rowcount != 1:
            return "fenced"
        st = conn.execute("SELECT state FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
    return "cancel" if st == TaskState.CANCELLING.value else "ok"


def record_child(conn: sqlite3.Connection, run_id: str, pid: int, create_time: float, device_label: str) -> None:
    with tx(conn):
        conn.execute("UPDATE runs SET pid=?, pid_create_time=?, device_label=? WHERE id=?",
                     (pid, create_time, device_label, run_id))


def finish(conn: sqlite3.Connection, claim_: Claim, worker_id: str, outcome: TaskOutcome) -> bool:
    """Publish the outcome if and only if this worker still holds the lease generation. Returns False if fenced."""
    tid = claim_.task["id"]
    with tx(conn):
        t = load(conn, tid)
        if (t["lease_owner"] != worker_id or int(t["lease_generation"]) != claim_.generation
                or t["state"] not in (TaskState.RUNNING.value, TaskState.CANCELLING.value)):
            emit(conn, "task.stale_result_dropped", actor=f"worker:{worker_id}", entity_type="task", entity_id=tid,
                 data={"generation": claim_.generation, "current_generation": t["lease_generation"]})
            return False
        dst = outcome.state
        if t["state"] == TaskState.RUNNING.value and dst is TaskState.CANCELLED:
            dst = TaskState.FAILED  # cannot cancel without a cancel request; handler bug
        transition(conn, t, dst, actor=f"worker:{worker_id}", reason=outcome.error_code or "",
                   fields={"lease_owner": None, "lease_expires_at": None, "result": outcome.result,
                           "error_code": outcome.error_code, "error_message": outcome.error_message})
        conn.execute("UPDATE runs SET state=?, ended_at=?, result=? WHERE id=?",
                     (dst.value, utcnow(), jdump(outcome.result), claim_.run_id))
    return True


def _backoff_s(attempt: int) -> float:
    return float(min(300, 5 * 2 ** max(0, attempt - 1)))


def _release_child(conn: sqlite3.Connection, task_id: str, generation: int) -> bool:
    """Make sure the dead worker's controlled child is gone. False = could not confirm (device stays locked)."""
    r = row(conn.execute("SELECT pid, pid_create_time FROM runs WHERE task_id=? AND lease_generation=?",
                         (task_id, generation)))
    if not r or not r["pid"]:
        return True
    if not pid_alive(int(r["pid"]), r["pid_create_time"]):
        return True
    return kill_tree(int(r["pid"]), r["pid_create_time"], grace_s=2.0)


def reap(conn: sqlite3.Connection, me: str, kinds: KindRegistry, worker_stale_s: float) -> list[str]:
    """Recover tasks whose lease expired and whose worker is not alive. Never touches this worker's own tasks."""
    now = utcnow()
    stale_before = (datetime.now(UTC) - timedelta(seconds=worker_stale_s)).isoformat(timespec="milliseconds")
    cands = rows(conn.execute(
        "SELECT t.* FROM tasks t LEFT JOIN workers w ON w.id=t.lease_owner WHERE t.state IN ('running','cancelling') "
        "AND t.lease_expires_at < ? AND t.lease_owner != ? AND (w.id IS NULL OR w.stopped_at IS NOT NULL "
        "OR w.heartbeat_at < ?)", (now, me, stale_before)))
    reaped = []
    for t in cands:
        released = _release_child(conn, t["id"], int(t["lease_generation"]))
        spec = kinds.get(t["kind"])
        with tx(conn):
            cur = load(conn, t["id"])
            if cur["lease_generation"] != t["lease_generation"] or cur["state"] != t["state"]:
                continue  # changed meanwhile
            clear: dict[str, object] = {"lease_owner": None, "lease_expires_at": None}
            if not released:
                dst, reason = TaskState.BLOCKED, "previous process still alive; check manually"
                if cur["state"] == TaskState.CANCELLING.value:
                    dst, reason = TaskState.INTERRUPTED, "cancel requested; process state unknown"
            elif cur["state"] == TaskState.CANCELLING.value:
                dst, reason = TaskState.CANCELLED, "worker lost during cancel"
            elif (spec and spec.retry is RetryPolicy.IDEMPOTENT and cur["resource_class"] != ResourceClass.GPU.value
                  and int(cur["attempt"]) < int(cur["max_attempts"])):
                dst, reason = TaskState.QUEUED, "lease expired; idempotent retry"
                clear["not_before"] = _iso_in(_backoff_s(int(cur["attempt"])))
            else:
                dst, reason = TaskState.INTERRUPTED, "lease expired; worker lost"
            transition(conn, cur, dst, actor=f"worker:{me}", reason=reason, fields=clear)
            conn.execute("UPDATE runs SET state='lost', ended_at=? WHERE task_id=? AND lease_generation=? "
                         "AND ended_at IS NULL", (utcnow(), t["id"], t["lease_generation"]))
        reaped.append(t["id"])
    return reaped
