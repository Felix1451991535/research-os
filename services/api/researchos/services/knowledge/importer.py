"""Import originals: hash -> dedupe (same bytes = same version) -> store read-only blob -> queue parsing.
Unsupported files are recorded with a reason and recovery action, never silently skipped (baseline §5)."""
from __future__ import annotations

import sqlite3
from typing import Any

from ...contracts.errors import ApiError
from ...kernel.db import Row, new_id, row, tx, utcnow
from ...kernel.events import emit
from ..content.blobs import BlobStore
from ..orchestration import tasks as task_svc
from ..orchestration.registry import KindRegistry
from .pdf import PARSER_VERSION

SUPPORTED = {b"%PDF": ("application/pdf", ".pdf")}


def sniff(data: bytes) -> tuple[str, str] | None:
    for magic, (mime, ext) in SUPPORTED.items():
        if data.startswith(magic):
            return mime, ext
    return None


def enqueue_parse(conn: sqlite3.Connection, kinds: KindRegistry, vid: str, *, actor: str, project_id: str | None,
                  cid: str | None) -> Row:
    """One parse task per (version, parser version): a document parsed by an older parser (e.g. before caption
    chunks, ADR-115) is re-parsed by the current one; for an up-to-date document the existing task is returned."""
    t, _ = task_svc.create(conn, kinds, kind="knowledge.parse_document", payload={"version_id": vid},
                           idempotency_key=f"parse:{vid}:{PARSER_VERSION}", actor=actor, project_id=project_id,
                           correlation_id=cid)
    return t


def import_document(conn: sqlite3.Connection, blobs: BlobStore, kinds: KindRegistry, *, data: bytes, filename: str,
                    project_id: str | None, actor: str, max_bytes: int, cid: str | None = None) -> dict[str, Any]:
    if len(data) > max_bytes:
        raise ApiError(413, "file_too_large", f"file is {len(data)} bytes; limit {max_bytes}")
    if not data:
        raise ApiError(422, "empty_file", "file is empty")
    kind = sniff(data)
    mime, ext = kind if kind else ("application/octet-stream", ".bin")
    sha, rel = blobs.put(data, ext)  # content-addressed + idempotent, safe before the DB transaction
    with tx(conn):
        existing = row(conn.execute("SELECT * FROM document_versions WHERE sha256=?", (sha,)))
        if existing:
            emit(conn, "document.duplicate", actor=actor, entity_type="document_version",
                 entity_id=existing["version_id"], project_id=project_id, correlation_id=cid,
                 data={"filename": filename})
            return {"version": existing, "created": False, "task_id": None}
        wid, vid, now = new_id("work"), new_id("ver"), utcnow()
        conn.execute("INSERT INTO works(id,project_id,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                     (wid, project_id, filename.rsplit(".", 1)[0][:300], now, now))
        conn.execute("INSERT INTO identifiers(work_id,scheme,value,source) VALUES (?,?,?,?)",
                     (wid, "sha256", sha, "import"))
        state = "pending" if kind else "unsupported"
        conn.execute("INSERT INTO document_versions(version_id,work_id,sha256,stored_path,original_name,size,mime,"
                     "parse_state,parse_error,recovery_action,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (vid, wid, sha, rel, filename[:300], len(data), mime, state,
                      None if kind else "unsupported file type",
                      None if kind else "v1 imports text PDFs; export the document as PDF", now))
        emit(conn, "document.imported", actor=actor, entity_type="document_version", entity_id=vid,
             project_id=project_id, correlation_id=cid, data={"filename": filename, "sha256": sha, "mime": mime})
    task_id = enqueue_parse(conn, kinds, vid, actor=actor, project_id=project_id, cid=cid)["id"] if kind else None
    v: Row = row(conn.execute("SELECT * FROM document_versions WHERE version_id=?", (vid,))) or {}
    return {"version": v, "created": True, "task_id": task_id}
