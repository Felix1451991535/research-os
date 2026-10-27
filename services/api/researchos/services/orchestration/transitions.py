"""The only place task state changes. Every transition checks the state table and writes an event in the
caller's transaction."""
from __future__ import annotations

import sqlite3
from typing import Any

from ...contracts.errors import ApiError
from ...contracts.tasks import TaskState, can_transition
from ...kernel.db import Row, jdump, row, utcnow
from ...kernel.events import emit


def load(conn: sqlite3.Connection, task_id: str) -> Row:
    t = row(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)))
    if not t:
        raise ApiError(404, "not_found", "task not found")
    return t


def transition(conn: sqlite3.Connection, task: Row, dst: TaskState, *, actor: str, reason: str = "",
               fields: dict[str, Any] | None = None, correlation_id: str | None = None) -> None:
    """Must be called inside a write transaction. Raises 409 on an illegal transition."""
    src = TaskState(task["state"])
    if not can_transition(src, dst):
        raise ApiError(409, "illegal_transition", f"task {task['id']}: {src} -> {dst} not allowed",
                       details={"state": src.value})
    sets = {"state": dst.value, "updated_at": utcnow(), **(fields or {})}
    for k, v in list(sets.items()):
        if isinstance(v, dict):
            sets[k] = jdump(v)
    cols = ", ".join(f"{k}=?" for k in sets)
    cur = conn.execute(f"UPDATE tasks SET {cols} WHERE id=? AND state=?", (*sets.values(), task["id"], src.value))
    if cur.rowcount != 1:
        raise ApiError(409, "concurrent_update", "task changed concurrently; retry", retryable=True)
    emit(conn, f"task.{dst.value}", actor=actor, entity_type="task", entity_id=task["id"],
         project_id=task["project_id"], correlation_id=correlation_id or task.get("trace_id"),
         data={"from": src.value, "reason": reason, "lease_generation": task["lease_generation"]})
    task.update({"state": dst.value})
