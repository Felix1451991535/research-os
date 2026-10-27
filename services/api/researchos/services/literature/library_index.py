"""Library organisation inside data_dir (K1 relations): tags, collections, backlinks from notes/docs that cite a
work's citekey ({{cite:key}}), and global search across works, document text (FTS) and notes."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...kernel.db import jload, row, rows, tx, utcnow
from ...kernel.events import emit
from ..knowledge.retrieval import FtsRetriever
from . import weight

NAME = re.compile(r"^[\w\- .:/]{1,80}$", re.UNICODE)
REL = {"tag": "tagged", "collection": "in_collection"}


def set_labels(conn: sqlite3.Connection, work_id: str, kind: str, names: list[str], actor: str) -> list[str]:
    """Replace a work's tags or collections (kind = tag | collection)."""
    if row(conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,))) is None:
        raise not_found("work")
    clean = sorted({n.strip() for n in names if n.strip()})
    bad = [n for n in clean if not NAME.match(n)]
    if bad:
        raise ApiError(422, "bad_label", f"invalid {kind} names {bad}")
    with tx(conn):
        conn.execute("DELETE FROM relations WHERE src=? AND type=?", (work_id, REL[kind]))
        for n in clean:
            conn.execute("INSERT INTO relations(src,type,dst,created_at) VALUES (?,?,?,?)",
                         (work_id, REL[kind], f"{kind}:{n}", utcnow()))
        emit(conn, f"work.{kind}s_set", actor=actor, entity_type="work", entity_id=work_id, data={"names": clean})
    return clean


def labels(conn: sqlite3.Connection, work_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"tags": [], "collections": []}
    for r in rows(conn.execute("SELECT type, dst FROM relations WHERE src=? AND type IN ('tagged','in_collection')",
                               (work_id,))):
        out["tags" if r["type"] == "tagged" else "collections"].append(r["dst"].split(":", 1)[1])
    return {k: sorted(v) for k, v in out.items()}


def list_works(conn: sqlite3.Connection, *, tag: str | None = None, collection: str | None = None,
               retracted: bool | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q, args = "SELECT w.*, c.citekey FROM works w LEFT JOIN citekeys c ON c.work_id=w.id WHERE 1=1", list[object]()
    for kind, val in (("tag", tag), ("collection", collection)):
        if val:
            q += " AND EXISTS (SELECT 1 FROM relations r WHERE r.src=w.id AND r.type=? AND r.dst=?)"
            args += [REL[kind], f"{kind}:{val}"]
    if retracted is not None:
        q += " AND json_extract(w.retraction,'$.retracted')=?" if retracted else \
             " AND COALESCE(json_extract(w.retraction,'$.retracted'),0)=0"
        args += [1] if retracted else []
    q += " ORDER BY w.updated_at DESC LIMIT ?"
    tables = weight.load_tiers(conn)
    return [{**w, "authors": jload(w["authors"]), "metadata": jload(w["metadata"]),
             "retraction": jload(w["retraction"]), **labels(conn, w["id"]),
             "weight": weight.weight(conn, w["id"], tables)["labels"]}
            for w in rows(conn.execute(q, [*args, limit]))]


def backlinks(conn: sqlite3.Connection, work_id: str) -> list[dict[str, Any]]:
    c = row(conn.execute("SELECT citekey FROM citekeys WHERE work_id=?", (work_id,)))
    if c is None:
        return []
    pat = f"%{{{{cite:{c['citekey']}}}}}%"
    return rows(conn.execute("SELECT id, project_id, kind, title, updated_at FROM docs WHERE body LIKE ? OR title "
                             "LIKE ? ORDER BY updated_at DESC", (pat, pat)))


def global_search(conn: sqlite3.Connection, q: str, project_id: str | None = None, limit: int = 20
                  ) -> dict[str, Any]:
    like = f"%{q.strip()}%"
    if len(q.strip()) < 2:
        raise ApiError(422, "query_too_short", "type at least 2 characters")
    works = rows(conn.execute("SELECT w.id, w.title, w.year, w.venue, c.citekey FROM works w LEFT JOIN citekeys c ON "
                              "c.work_id=w.id WHERE w.title LIKE ? OR w.authors LIKE ? OR w.venue LIKE ? OR "
                              "c.citekey LIKE ? LIMIT ?", (like, like, like, like, limit)))
    docs = rows(conn.execute("SELECT id, kind, title, project_id FROM docs WHERE (title LIKE ? OR body LIKE ?)"
                             + (" AND project_id=?" if project_id else "") + " LIMIT ?",
                             (like, like, *([project_id] if project_id else []), limit)))
    passages = [h.__dict__ for h in FtsRetriever(conn).search(q, project_id=project_id, limit=limit)]
    return {"works": works, "docs": docs, "passages": passages}
