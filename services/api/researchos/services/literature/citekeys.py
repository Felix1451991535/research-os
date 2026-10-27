"""Citekeys `author+year+word` (+a/b/... on collision), stable once assigned, bound to Works and through them to
documents and Evidence. Non-Latin first authors: `ref<year>-<hash4>` (pinyin romanization is plugin scope)."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import unicodedata
from typing import Any

from ...contracts.errors import not_found
from ...kernel.db import jload, row, rows, tx, utcnow
from ...kernel.events import emit
from ..content.citekey_index import lookup_work
from . import weight

STOP = {"a", "an", "the", "of", "on", "in", "for", "and", "to", "with", "by", "from", "at", "is", "are", "towards"}


def _ascii(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())


def base(work: dict[str, Any]) -> str:
    authors = jload(work["authors"]) if isinstance(work["authors"], str) else work["authors"]
    first = (authors or [""])[0]
    surname = _ascii(first.split(",")[0] if "," in first else (first.split() or [""])[-1])
    year = str(work["year"]) if work.get("year") else "nd"
    if not surname and first:
        return f"ref{year}-{hashlib.sha256(work['id'].encode()).hexdigest()[:4]}"
    word = next((w for w in (_ascii(t) for t in str(work.get("title") or "").split()) if w and w not in STOP), "")
    return f"{surname or 'anon'}{year}{word}"


def assign(conn: sqlite3.Connection, work_id: str, actor: str = "system") -> str:
    """Joins an existing write transaction when present. Idempotent."""
    have = row(conn.execute("SELECT citekey FROM citekeys WHERE work_id=?", (work_id,)))
    if have:
        return str(have["citekey"])
    w = row(conn.execute("SELECT id, authors, year, title FROM works WHERE id=?", (work_id,)))
    if w is None:
        raise not_found("work")
    b = base(w)
    with tx(conn, join=True):
        taken = {r["citekey"] for r in rows(conn.execute("SELECT citekey FROM citekeys WHERE citekey LIKE ?",
                                                          (b + "%",)))}
        key = b if b not in taken else next(f"{b}{chr(c)}" for c in range(ord("a"), ord("z") + 1)
                                            if f"{b}{chr(c)}" not in taken)
        conn.execute("INSERT INTO citekeys(work_id,citekey,created_at) VALUES (?,?,?)", (work_id, key, utcnow()))
        emit(conn, "work.citekey_assigned", actor=actor, entity_type="work", entity_id=work_id, data={"citekey": key})
    return key


def resolve(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    """citekey -> Work, its document versions and its Evidence (the binding used by citation gates in writing)."""
    w = lookup_work(conn, key)
    if w is None:
        raise not_found("citekey")
    versions = rows(conn.execute("SELECT version_id, parse_state, license FROM document_versions WHERE work_id=?",
                                 (w["id"],)))
    ev = rows(conn.execute("SELECT e.id, e.page_index, e.trust, e.kind FROM evidence e JOIN document_versions v ON "
                           "v.version_id=e.version_id WHERE v.work_id=? ORDER BY e.created_at", (w["id"],)))
    return {"citekey": key, "work": {**w, "authors": jload(w["authors"]), "metadata": jload(w["metadata"]),
                                     "retraction": jload(w["retraction"])},
            "versions": versions, "evidence": ev, "weight": weight.weight(conn, w["id"])["labels"]}


def keys_for_evidence(conn: sqlite3.Connection, ev_ids: list[str]) -> list[str]:
    """Citekeys (assigned on demand) of the works the given Evidence comes from, sorted by work id."""
    if not ev_ids:
        return []
    works = sorted({r["work_id"] for r in rows(conn.execute(
        "SELECT DISTINCT v.work_id FROM evidence e JOIN document_versions v ON v.version_id=e.version_id "
        f"WHERE e.id IN ({','.join('?' * len(ev_ids))})", ev_ids))})
    return [assign(conn, w) for w in works]


def cited_markdown(conn: sqlite3.Connection, text: str, ev_ids: list[str]) -> str:
    """The one way a verified sentence enters a doc: text + `{{evidence:}}` per Evidence + `{{cite:}}` per work."""
    ev = "".join(f"{{{{evidence:{e}}}}}" for e in ev_ids)
    cites = "".join(f"{{{{cite:{k}}}}}" for k in keys_for_evidence(conn, ev_ids))
    return f"{text} {ev} {cites}".rstrip()
