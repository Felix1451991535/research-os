"""One approval mechanism (K3): bound to subject type + id + content hash, scope, limits, expiry, device, re-auth.

Offline approvals never auto-apply: an approval only exists once this endpoint commits it on the server.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from ...contracts.errors import ApiError
from ...contracts.tasks import TaskState
from ...kernel.db import Row, jdump, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from ..identity.sessions import Principal
from ..orchestration.transitions import load, transition
from .subjects import SubjectRegistry


def task_subject_hash(conn: sqlite3.Connection, task_id: str) -> str:
    t = load(conn, task_id)
    return hashlib.sha256(f"{t['kind']}|{t['payload_hash']}".encode()).hexdigest()


def current_hash(conn: sqlite3.Connection, subjects: SubjectRegistry, subject_type: str, subject_id: str) -> str:
    reg = subjects.get(subject_type)
    if reg is None:
        raise ApiError(422, "unknown_subject", f"unknown approval subject {subject_type!r}")
    if reg.resolver is None:
        raise ApiError(422, "subject_not_resolvable", f"subject {subject_type!r} has no resolver yet")
    return reg.resolver(conn, subject_id)


def approve(conn: sqlite3.Connection, subjects: SubjectRegistry, p: Principal, *, subject_type: str,
            subject_id: str, expected_hash: str | None = None, scope: dict[str, Any] | None = None,
            limits: dict[str, Any] | None = None, correlation_id: str | None = None) -> Row:
    reg = subjects.get(subject_type)
    if reg is None:
        raise ApiError(422, "unknown_subject", f"unknown approval subject {subject_type!r}")
    needs_reauth = reg.spec.reauth_when_remote and not p.is_host
    if needs_reauth and not p.recently_reauthenticated():
        raise ApiError(403, "reauth_required", "re-enter your password to approve this from a remote device")
    with tx(conn):
        h = current_hash(conn, subjects, subject_type, subject_id)
        if expected_hash and expected_hash != h:
            raise ApiError(409, "subject_changed", "the subject changed since you reviewed it; review again",
                           details={"current_hash": h})
        aid, now = new_id("apr"), datetime.now(UTC)
        conn.execute("INSERT INTO approvals(id,subject_type,subject_id,subject_hash,scope,limits,approved_by_device,"
                     "reauth,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (aid, subject_type, subject_id, h, jdump(scope or {}), jdump(limits or {}), p.device_id,
                      int(needs_reauth), utcnow(),
                      (now + timedelta(hours=reg.spec.ttl_hours)).isoformat(timespec="milliseconds")))
        emit(conn, "approval.granted", actor=p.actor, entity_type=subject_type, entity_id=subject_id,
             correlation_id=correlation_id, data={"approval_id": aid, "hash": h})
        if subject_type == "task":
            t = load(conn, subject_id)
            if t["state"] in (TaskState.AWAITING_APPROVAL.value, TaskState.BLOCKED.value):
                transition(conn, t, TaskState.QUEUED, actor=p.actor, reason="approved",
                           fields={"approval_id": aid}, correlation_id=correlation_id)
                conn.execute("UPDATE approvals SET consumed_at=? WHERE id=?", (utcnow(), aid))
        return dict(row(conn.execute("SELECT * FROM approvals WHERE id=?", (aid,))) or {})


def is_valid(conn: sqlite3.Connection, subjects: SubjectRegistry, approval_id: str) -> bool:
    a = row(conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)))
    if not a or a["revoked_at"]:
        return False
    if datetime.fromisoformat(a["expires_at"]) <= datetime.now(UTC) and not a["consumed_at"]:
        return False
    return bool(current_hash(conn, subjects, a["subject_type"], a["subject_id"]) == a["subject_hash"])


def revoke(conn: sqlite3.Connection, approval_id: str, p: Principal) -> None:
    """Revoking the approval of a task that has not started blocks it again."""
    with tx(conn):
        a = row(conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)))
        if not a:
            raise ApiError(404, "not_found", "approval not found")
        if a["revoked_at"]:
            return
        conn.execute("UPDATE approvals SET revoked_at=? WHERE id=?", (utcnow(), approval_id))
        emit(conn, "approval.revoked", actor=p.actor, entity_type=a["subject_type"], entity_id=a["subject_id"],
             data={"approval_id": approval_id})
        if a["subject_type"] == "task":
            t = load(conn, a["subject_id"])
            if t["state"] == TaskState.QUEUED.value and t["approval_id"] == approval_id:
                transition(conn, t, TaskState.BLOCKED, actor=p.actor, reason="approval revoked")


def list_for(conn: sqlite3.Connection, subject_type: str, subject_id: str) -> list[Row]:
    return rows(conn.execute("SELECT * FROM approvals WHERE subject_type=? AND subject_id=? ORDER BY created_at",
                             (subject_type, subject_id)))
