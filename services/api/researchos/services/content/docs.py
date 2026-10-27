"""K8 authored documents. M0 implements kind `note` (offline-editable drafts, baseline §3).

Version conflicts never silently overwrite: the incoming text is kept as a conflict copy and 409 returns both.
The copy is keyed by (conflict_of, client_operation_id) so an offline client re-sending the same edit gets the
same copy back instead of a new duplicate.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from ...contracts.docs import DocKindSpec
from ...contracts.errors import ApiError, not_found
from ...kernel.db import Row, new_id, row, rows, tx, utcnow
from ...kernel.events import emit

CORE_DOC_KINDS = (
    DocKindSpec("note", "free-text note / offline draft"),
    DocKindSpec("report", "generated or authored report with {{cite:}} / {{evidence:}} / {{metric:}} placeholders"),
    DocKindSpec("manuscript", "paper draft; exports pass the citation + number gates"),
    DocKindSpec("paper_card", "fixed per-paper note card (cited fields; 'relation to my work' is the owner's)"),
    DocKindSpec("methods_checklist", "per-paper methods checklist (cited items + deterministic missing conditions)"),
    DocKindSpec("feasibility", "topic feasibility check (cited literature + recorded data/compute/time facts)"),
)


class DocKindRegistry:
    def __init__(self, specs: Iterable[DocKindSpec] = CORE_DOC_KINDS) -> None:
        self._s = {s.name: s for s in specs}
        self._frozen = False

    def freeze(self) -> DocKindRegistry:
        self._frozen = True
        return self

    def add(self, spec: DocKindSpec) -> None:
        if self._frozen:
            raise RuntimeError("registry is frozen after startup")
        if spec.name in self._s:
            raise ValueError(f"duplicate doc kind {spec.name}")
        self._s[spec.name] = spec

    def get(self, name: str) -> DocKindSpec | None:
        return self._s.get(name)


def get(conn: sqlite3.Connection, doc_id: str) -> Row:
    d = row(conn.execute("SELECT * FROM docs WHERE id=?", (doc_id,)))
    if not d:
        raise not_found("doc")
    return d


def list_for_project(conn: sqlite3.Connection, project_id: str, kind: str | None = None) -> list[Row]:
    if kind:
        return rows(
            conn.execute(
                "SELECT * FROM docs WHERE project_id=? AND kind=? ORDER BY updated_at DESC", (project_id, kind)
            )
        )
    return rows(conn.execute("SELECT * FROM docs WHERE project_id=? ORDER BY updated_at DESC", (project_id,)))


def create_in_tx(
    conn: sqlite3.Connection,
    kinds: DocKindRegistry,
    project_id: str,
    kind: str,
    title: str,
    body: str,
    actor: str,
    client_operation_id: str | None = None,
    cid: str | None = None,
) -> Row:
    if kinds.get(kind) is None:
        raise ApiError(422, "unknown_doc_kind", f"unknown doc kind {kind!r}")
    did, now = new_id("doc"), utcnow()
    conn.execute(
        "INSERT INTO docs(id,project_id,kind,title,body,client_operation_id,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (did, project_id, kind, title, body, client_operation_id, now, now),
    )
    conn.execute(
        "INSERT INTO doc_revisions(doc_id,version,title,body,actor,created_at) VALUES (?,?,?,?,?,?)",
        (did, 1, title, body, actor, now),
    )
    emit(
        conn,
        "doc.created",
        actor=actor,
        entity_type="doc",
        entity_id=did,
        project_id=project_id,
        correlation_id=cid,
        data={"kind": kind, "client_operation_id": client_operation_id},
    )
    return get(conn, did)


def update(
    conn: sqlite3.Connection,
    doc_id: str,
    *,
    title: str,
    body: str,
    base_version: int,
    actor: str,
    client_operation_id: str | None,
    cid: str | None = None,
) -> Row:
    conflict_payload: dict[str, object] | None = None
    with tx(conn, join=True):
        d = get(conn, doc_id)
        if d["version"] == base_version:
            now = utcnow()
            conn.execute(
                "UPDATE docs SET title=?, body=?, version=version+1, updated_at=? WHERE id=?",
                (title, body, now, doc_id),
            )
            conn.execute(
                "INSERT INTO doc_revisions(doc_id,version,title,body,actor,created_at) VALUES (?,?,?,?,?,?)",
                (doc_id, d["version"] + 1, title, body, actor, now),
            )
            emit(
                conn,
                "doc.updated",
                actor=actor,
                entity_type="doc",
                entity_id=doc_id,
                project_id=d["project_id"],
                correlation_id=cid,
                data={"version": d["version"] + 1},
            )
            return get(conn, doc_id)
        if d["title"] == title and d["body"] == body:
            return d  # identical content: idempotent re-sync
        copy = None
        if client_operation_id:
            copy = row(
                conn.execute(
                    "SELECT * FROM docs WHERE conflict_of=? AND client_operation_id=?", (doc_id, client_operation_id)
                )
            )
        if copy is None:
            cid_, now = new_id("doc"), utcnow()
            conn.execute(
                "INSERT INTO docs(id,project_id,kind,title,body,conflict_of,client_operation_id,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (cid_, d["project_id"], d["kind"], title or d["title"], body, doc_id, client_operation_id, now, now),
            )
            emit(
                conn,
                "doc.conflict",
                actor=actor,
                entity_type="doc",
                entity_id=doc_id,
                project_id=d["project_id"],
                correlation_id=cid,
                data={"conflict_copy": cid_, "base_version": base_version, "server_version": d["version"]},
            )
            copy = get(conn, cid_)
        conflict_payload = {"server": d, "conflict_copy_id": copy["id"], "client_body": body}
    raise ApiError(
        409,
        "version_conflict",
        "document changed on server; both versions kept, merge required",
        details=conflict_payload,
    )
