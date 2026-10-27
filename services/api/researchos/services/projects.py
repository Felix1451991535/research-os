"""Projects (K1 domain object) with optimistic concurrency via revision."""
from __future__ import annotations

import sqlite3
from typing import Any

from ..contracts.errors import ApiError, conflict, not_found
from ..kernel.db import Row, new_id, row, rows, tx, utcnow
from ..kernel.events import emit

EDITABLE = ("title", "goal", "status", "next_step")


def get(conn: sqlite3.Connection, pid: str) -> Row:
    p = row(conn.execute("SELECT * FROM projects WHERE id=?", (pid,)))
    if not p:
        raise not_found("project")
    return p


def create_in_tx(conn: sqlite3.Connection, title: str, goal: str, actor: str, cid: str | None = None) -> Row:
    pid, now = new_id("prj"), utcnow()
    conn.execute("INSERT INTO projects(id,title,goal,created_at,updated_at) VALUES (?,?,?,?,?)",
                 (pid, title, goal, now, now))
    emit(conn, "project.created", actor=actor, entity_type="project", entity_id=pid, project_id=pid,
         correlation_id=cid, data={"title": title})
    return get(conn, pid)


def list_all(conn: sqlite3.Connection) -> list[Row]:
    return rows(conn.execute("SELECT * FROM projects ORDER BY status='archived', updated_at DESC"))


def update(conn: sqlite3.Connection, pid: str, revision: int, changes: dict[str, Any], actor: str,
           cid: str | None = None) -> Row:
    bad = set(changes) - set(EDITABLE)
    if bad:
        raise ApiError(422, "invalid_fields", f"not editable: {sorted(bad)}")
    with tx(conn):
        cur = get(conn, pid)
        if cur["revision"] != revision:
            raise conflict("revision_conflict", "project was modified elsewhere; reload",
                           {"current_revision": cur["revision"]})
        if changes:
            cols = ", ".join(f"{k}=?" for k in changes)  # keys validated against EDITABLE above
            conn.execute(f"UPDATE projects SET {cols}, revision=revision+1, updated_at=? WHERE id=?",
                         (*changes.values(), utcnow(), pid))
            emit(conn, "project.updated", actor=actor, entity_type="project", entity_id=pid, project_id=pid,
                 correlation_id=cid, data={"fields": sorted(changes)})
        return get(conn, pid)
