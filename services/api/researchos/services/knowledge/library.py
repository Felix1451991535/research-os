"""Read models for the Library UI: documents with parse status, pages, chunks, identifiers."""
from __future__ import annotations

import sqlite3
from typing import Any

from ...contracts.errors import not_found
from ...kernel.db import jload, row, rows

_LIST = ("SELECT v.version_id, v.work_id, v.original_name, v.size, v.mime, v.parse_state, v.parse_error, "
         "v.recovery_action, v.page_count, v.parser_version, v.created_at, w.title, w.project_id, "
         "(SELECT state FROM tasks t WHERE t.idempotency_key LIKE 'parse:' || v.version_id || ':%' "
         " ORDER BY t.created_at DESC LIMIT 1) AS task_state, "
         "(SELECT value FROM identifiers i WHERE i.work_id=w.id AND i.scheme='doi') AS doi "
         "FROM document_versions v JOIN works w ON w.id=v.work_id")


def list_documents(conn: sqlite3.Connection, project_id: str | None) -> list[dict[str, Any]]:
    if project_id:
        return rows(conn.execute(_LIST + " WHERE w.project_id=? ORDER BY v.created_at DESC", (project_id,)))
    return rows(conn.execute(_LIST + " ORDER BY v.created_at DESC"))


def get_document(conn: sqlite3.Connection, version_id: str) -> dict[str, Any]:
    d = row(conn.execute(_LIST + " WHERE v.version_id=?", (version_id,)))
    if d is None:
        raise not_found("document")
    d["identifiers"] = rows(conn.execute("SELECT scheme, value, source FROM identifiers WHERE work_id=?",
                                         (d["work_id"],)))
    pages = rows(conn.execute("SELECT page_index, page_label, quality, flags, char_count FROM pages WHERE version_id=? "
                              "ORDER BY page_index", (version_id,)))
    for p in pages:
        p["flags"] = jload(p["flags"]) or []
    d["pages"] = pages
    return d


def get_page(conn: sqlite3.Connection, version_id: str, page_index: int) -> dict[str, Any]:
    p = row(conn.execute("SELECT * FROM pages WHERE version_id=? AND page_index=?", (version_id, page_index)))
    if p is None:
        raise not_found("page")
    p["flags"] = jload(p["flags"]) or []
    chunks = rows(conn.execute("SELECT chunk_id, char_start, char_end, bbox, section_role, chunk_kind FROM chunks "
                               "WHERE version_id=? AND page_index=? ORDER BY seq", (version_id, page_index)))
    for c in chunks:
        c["bbox"] = jload(c["bbox"])
    p["chunks"] = chunks
    return p
