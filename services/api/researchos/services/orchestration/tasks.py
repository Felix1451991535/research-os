"""Task creation, reading, cancellation and explicit retry (API-side K2 operations)."""
from __future__ import annotations

import sqlite3
from typing import Any

import jsonschema

from ...contracts.errors import ApiError
from ...contracts.tasks import TERMINAL, TaskState
from ...kernel.db import Row, jdump, jload, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from ...kernel.idempotency import request_hash
from .registry import KindRegistry
from .transitions import load, transition


def public(t: Row) -> Row:
    out = {k: t[k] for k in ("id", "kind", "project_id", "parent_id", "trace_id", "state", "resource_class", "attempt",
                             "max_attempts", "lease_generation", "stage", "error_code", "error_message",
                             "approval_id", "created_at", "updated_at", "cancel_requested_at")}
    out["payload"] = jload(t["payload"]) or {}
    out["progress"] = jload(t["progress"]) or {}
    out["result"] = jload(t["result"])
    return out


def _trace_for(conn: sqlite3.Connection, parent_id: str | None, correlation_id: str | None) -> str:
    """Children inherit the parent's trace; a top-level task's trace is the creating request's correlation id."""
    if parent_id:
        return str(load(conn, parent_id)["trace_id"])
    return correlation_id or new_id("trace")


def create(conn: sqlite3.Connection, kinds: KindRegistry, *, kind: str, payload: dict[str, Any],
           idempotency_key: str, actor: str, project_id: str | None = None, parent_id: str | None = None,
           priority: int = 0, correlation_id: str | None = None) -> tuple[Row, bool]:
    """Returns (task, created). Same idempotency key + same request => the existing task (one logical task)."""
    spec = kinds.get(kind)
    if spec is None:
        raise ApiError(422, "unknown_task_kind", f"unknown task kind {kind!r}")
    if spec.payload_schema:
        try:
            jsonschema.validate(payload, spec.payload_schema)
        except jsonschema.ValidationError as e:
            raise ApiError(422, "invalid_payload", e.message) from e
    phash = request_hash({"kind": kind, "payload": payload, "project_id": project_id, "parent_id": parent_id})
    with tx(conn):
        existing = row(conn.execute("SELECT * FROM tasks WHERE idempotency_key=?", (idempotency_key,)))
        if existing:
            if existing["payload_hash"] != phash:
                raise ApiError(422, "idempotency_mismatch", "Idempotency-Key reused for a different task")
            return public(existing), False
        tid, now = new_id("task"), utcnow()
        trace_id = _trace_for(conn, parent_id, correlation_id)
        state = TaskState.AWAITING_APPROVAL if spec.approval_subject else TaskState.QUEUED
        conn.execute(
            "INSERT INTO tasks(id,kind,project_id,parent_id,idempotency_key,trace_id,state,payload,payload_hash,"
            "resource_class,priority,max_attempts,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, kind, project_id, parent_id, idempotency_key, trace_id, state.value, jdump(payload), phash,
             spec.resource_class.value, priority, spec.max_attempts, now, now))
        emit(conn, f"task.{state.value}", actor=actor, entity_type="task", entity_id=tid, project_id=project_id,
             correlation_id=trace_id, data={"kind": kind, "created": True})
        return public(load(conn, tid)), True


def get(conn: sqlite3.Connection, task_id: str) -> Row:
    return public(load(conn, task_id))


def list_tasks(conn: sqlite3.Connection, project_id: str | None = None, state: str | None = None,
               limit: int = 100) -> list[Row]:
    sql, args = "SELECT * FROM tasks WHERE 1=1", []
    if project_id:
        sql += " AND project_id=?"
        args.append(project_id)
    if state:
        sql += " AND state=?"
        args.append(state)
    sql += " ORDER BY created_at DESC LIMIT ?"
    return [public(t) for t in rows(conn.execute(sql, (*args, limit)))]


def cancel(conn: sqlite3.Connection, task_id: str, actor: str, correlation_id: str | None = None) -> Row:
    """Not-yet-running tasks are cancelled at once; running ones move to CANCELLING and the worker stops them."""
    with tx(conn):
        t = load(conn, task_id)
        st = TaskState(t["state"])
        if st in TERMINAL or st is TaskState.CANCELLING:
            return public(t)
        if st is TaskState.RUNNING:
            transition(conn, t, TaskState.CANCELLING, actor=actor, reason="user cancel",
                       fields={"cancel_requested_at": utcnow()}, correlation_id=correlation_id)
        else:
            transition(conn, t, TaskState.CANCELLED, actor=actor, reason="user cancel",
                       fields={"cancel_requested_at": utcnow()}, correlation_id=correlation_id)
        return public(load(conn, task_id))


def retry(conn: sqlite3.Connection, task_id: str, actor: str) -> Row:
    """Explicit user retry of an interrupted or blocked task (never automatic for these states)."""
    with tx(conn):
        t = load(conn, task_id)
        if t["state"] not in (TaskState.INTERRUPTED.value, TaskState.BLOCKED.value):
            raise ApiError(409, "not_retryable", f"task is {t['state']}")
        if t["attempt"] >= t["max_attempts"]:
            conn.execute("UPDATE tasks SET max_attempts=attempt+1 WHERE id=?", (task_id,))
            emit(conn, "task.attempts_extended", actor=actor, entity_type="task", entity_id=task_id)
        transition(conn, t, TaskState.QUEUED, actor=actor, reason="user retry",
                   fields={"lease_owner": None, "lease_expires_at": None, "not_before": None})
        return public(load(conn, task_id))
