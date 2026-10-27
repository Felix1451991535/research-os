"""Suggestion mode (K8): proposals never change a doc; an owner decision applies one as a new revision.
`replace`/`insert_after` need their anchor to occur exactly once in the CURRENT body (else status `conflict`,
nothing changes); `append` adds at the end. Cited answers enter docs only through this path."""

from __future__ import annotations

import sqlite3
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...kernel.db import Row, jdump, jload, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from ..content import docs


def propose(conn: sqlite3.Connection, doc_id: str, *, op: str, text: str, actor: str, anchor: str | None = None,
            rationale: str = "", source: dict[str, Any] | None = None) -> Row:
    d = docs.get(conn, doc_id)
    if op not in ("replace", "insert_after", "append") or (op != "append" and not anchor) or not text.strip():
        raise ApiError(422, "bad_suggestion", "op replace|insert_after need an anchor; text must be non-empty")
    sid = new_id("sug")
    with tx(conn, join=True):
        conn.execute("INSERT INTO doc_suggestions(id,doc_id,base_version,op,anchor,text,rationale,source,author,status,"
                     "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (sid, doc_id, d["version"], op, anchor, text, rationale, jdump(source or {}), actor, "proposed",
                      utcnow()))
        emit(conn, "doc.suggestion_proposed", actor=actor, entity_type="doc", entity_id=doc_id,
             project_id=d["project_id"], data={"suggestion": sid, "op": op})
    return get(conn, sid)


def get(conn: sqlite3.Connection, sid: str) -> Row:
    s = row(conn.execute("SELECT * FROM doc_suggestions WHERE id=?", (sid,)))
    if s is None:
        raise not_found("suggestion")
    return {**s, "source": jload(s["source"])}


def list_for(conn: sqlite3.Connection, doc_id: str, status: str | None = None) -> list[Row]:
    q = "SELECT * FROM doc_suggestions WHERE doc_id=?" + (" AND status=?" if status else "") + " ORDER BY created_at"
    args = (doc_id, status) if status else (doc_id,)
    return [{**s, "source": jload(s["source"])} for s in rows(conn.execute(q, args))]


def _apply(body: str, s: Row) -> str | None:
    text, anchor = str(s["text"]), str(s["anchor"] or "")
    if s["op"] == "append":
        return body.rstrip("\n") + "\n\n" + text.strip("\n") + "\n"
    if body.count(anchor) != 1:
        return None
    return body.replace(anchor, text if s["op"] == "replace" else anchor + text, 1)


def _close(conn: sqlite3.Connection, s: Row, status: str, actor: str) -> None:
    """Atomic claim: only a still-`proposed` suggestion can be decided (check and write in one transaction)."""
    with tx(conn, join=True):
        cur = conn.execute("UPDATE doc_suggestions SET status=?, decided_by=?, decided_at=? WHERE id=? AND "
                           "status='proposed'", (status, actor, utcnow(), s["id"]))
        if cur.rowcount != 1:
            raise ApiError(409, "already_decided", "suggestion was decided concurrently")
        emit(conn, f"doc.suggestion_{status}", actor=actor, entity_type="doc", entity_id=s["doc_id"],
             data={"suggestion": s["id"]})


def decide(conn: sqlite3.Connection, sid: str, accept: bool, actor: str) -> dict[str, Any]:
    with tx(conn, join=True):
        s = get(conn, sid)
        if s["status"] != "proposed":
            raise ApiError(409, "already_decided", f"suggestion is {s['status']}")
        if not accept:
            _close(conn, s, "rejected", actor)
            return {"status": "rejected"}
        d = docs.get(conn, s["doc_id"])
        new = _apply(d["body"], s)
        if new is None:
            _close(conn, s, "conflict", actor)
            return {"status": "conflict", "reason": "anchor text no longer occurs exactly once; re-propose"}
        _close(conn, s, "accepted", actor)
        doc = docs.update(conn, d["id"], title=d["title"], body=new, base_version=d["version"], actor=actor,
                          client_operation_id=None, cid=None)
        return {"status": "accepted", "doc": doc}
