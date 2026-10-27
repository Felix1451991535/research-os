"""Experiment plans as immutable revisions (K5). Approval binds to the revision content hash; any change is a new
revision. Narrowing a search space inside the approved one is allowed automatically (ADR-016); widening or
changing metric/data/template/split needs a fresh approval."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

import jsonschema

from ...contracts.errors import ApiError, not_found
from ...kernel.db import Row, jdump, jload, new_id, row, rows, tx, utcnow
from ...kernel.events import emit

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["hypothesis", "template", "metrics", "limits"],
    "properties": {
        "hypothesis": {"type": "string", "minLength": 3},
        "predictions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["metric", "expected"],
                "properties": {
                    "metric": {"type": "string"},
                    "expected": {},
                    "prior": {"type": "number", "minimum": 0, "maximum": 1},
                },
            },
        },
        "template": {
            "type": "object",
            "required": ["id", "hash"],
            "properties": {"id": {"type": "string"}, "hash": {"type": "string"}},
        },
        "dataset": {"type": "object", "properties": {"id": {"type": "string"}, "manifest_sha256": {"type": "string"}}},
        "split": {"type": "object"},
        "params": {"type": "object"},
        "space": {"type": "object"},
        "metrics": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "limits": {
            "type": "object",
            "properties": {
                "max_minutes": {"type": "number", "maximum": 90, "minimum": 0.01},
                "memory_mb": {"type": "number"},
                "max_trials": {"type": "integer"},
                "stall_s": {"type": "number"},
            },
        },
    },
}


def content_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(jdump(content).encode()).hexdigest()


def _validate(content: dict[str, Any]) -> None:
    try:
        jsonschema.validate(content, PLAN_SCHEMA)
    except jsonschema.ValidationError as e:
        raise ApiError(422, "invalid_plan", e.message) from e


def create(
    conn: sqlite3.Connection, *, title: str, content: dict[str, Any], actor: str, project_id: str | None = None
) -> Row:
    _validate(content)
    pid = new_id("plan")
    with tx(conn):
        conn.execute(
            "INSERT INTO plans(id,project_id,title,created_at) VALUES (?,?,?,?)", (pid, project_id, title, utcnow())
        )
        _insert_revision(conn, pid, 1, content, None, "initial", actor, project_id)
    return get_revision(conn, pid, 1)


def _insert_revision(
    conn: sqlite3.Connection,
    pid: str,
    rev: int,
    content: dict[str, Any],
    parent: int | None,
    kind: str,
    actor: str,
    project_id: str | None,
) -> None:
    conn.execute(
        "INSERT INTO plan_revisions(plan_id,revision,content,content_hash,parent_revision,change_kind,"
        "created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (pid, rev, jdump(content), content_hash(content), parent, kind, actor, utcnow()),
    )
    emit(
        conn,
        "plan.revision_created",
        actor=actor,
        entity_type="plan",
        entity_id=pid,
        project_id=project_id,
        data={"revision": rev, "change_kind": kind, "hash": content_hash(content)},
    )


def is_narrowing(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """True if new differs from old ONLY by a search space whose every parameter range lies inside the old one."""
    if {k: v for k, v in old.items() if k != "space"} != {k: v for k, v in new.items() if k != "space"}:
        return False
    o, n = old.get("space") or {}, new.get("space") or {}
    if set(n) - set(o):
        return False
    for k, spec in n.items():
        osp = o[k]
        if "choices" in spec and not set(spec["choices"]) <= set(osp.get("choices", [])):
            return False
        if "low" in spec and not (
            osp.get("low", spec["low"]) <= spec["low"] <= spec["high"] <= osp.get("high", spec["high"])
        ):
            return False
    return True


def revise(conn: sqlite3.Connection, plan_id: str, content: dict[str, Any], actor: str) -> Row:
    _validate(content)
    with tx(conn):
        last = row(
            conn.execute("SELECT * FROM plan_revisions WHERE plan_id=? ORDER BY revision DESC LIMIT 1", (plan_id,))
        )
        if last is None:
            raise not_found("plan")
        kind = "narrowing" if is_narrowing(jload(last["content"]), content) else "other"
        project = row(conn.execute("SELECT project_id FROM plans WHERE id=?", (plan_id,)))
        _insert_revision(
            conn,
            plan_id,
            int(last["revision"]) + 1,
            content,
            int(last["revision"]),
            kind,
            actor,
            project["project_id"] if project else None,
        )
        rev = int(last["revision"]) + 1
    return get_revision(conn, plan_id, rev)


def get_revision(conn: sqlite3.Connection, plan_id: str, revision: int) -> Row:
    r = row(conn.execute("SELECT * FROM plan_revisions WHERE plan_id=? AND revision=?", (plan_id, revision)))
    if r is None:
        raise not_found("plan revision")
    r["content"] = jload(r["content"])
    return r


def list_plans(conn: sqlite3.Connection, project_id: str | None = None) -> list[Row]:
    sql = (
        "SELECT p.*, (SELECT MAX(revision) FROM plan_revisions r WHERE r.plan_id=p.id) AS latest_revision FROM plans p"
    )
    if project_id:
        return rows(conn.execute(sql + " WHERE p.project_id=? ORDER BY created_at DESC", (project_id,)))
    return rows(conn.execute(sql + " ORDER BY created_at DESC"))


def parse_subject(subject_id: str) -> tuple[str, int]:
    pid, _, rev = subject_id.partition("@")
    if not rev.isdigit():
        raise ApiError(422, "bad_subject", "plan subject id must look like '<plan_id>@<revision>'")
    return pid, int(rev)


def plan_subject_hash(conn: sqlite3.Connection, subject_id: str) -> str:
    pid, rev = parse_subject(subject_id)
    return str(get_revision(conn, pid, rev)["content_hash"])


def final_eval_subject_hash(conn: sqlite3.Connection, subject_id: str) -> str:
    return hashlib.sha256(f"final_eval|{plan_subject_hash(conn, subject_id)}".encode()).hexdigest()


def approval_covers(conn: sqlite3.Connection, plan_id: str, revision: int) -> int | None:
    """Revision whose approval covers this one: itself, or the nearest approved ancestor if every step in between
    was a narrowing. Returns None when no valid approval covers it."""
    rev = revision
    while True:
        a = row(
            conn.execute(
                "SELECT a.* FROM approvals a WHERE a.subject_type='plan' AND a.subject_id=? "
                "AND a.revoked_at IS NULL ORDER BY created_at DESC LIMIT 1",
                (f"{plan_id}@{rev}",),
            )
        )
        r = get_revision(conn, plan_id, rev)
        if a is not None and a["subject_hash"] == r["content_hash"]:
            return rev
        if r["change_kind"] != "narrowing" or r["parent_revision"] is None:
            return None
        rev = int(r["parent_revision"])
