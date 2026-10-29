"""Approved imports through plugin Importers. The K3 approval (subject `knowledge_import`) binds to the hash of the
exact selected records; the host checks and consumes it before the importer sees a KnowledgeWriter."""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import asdict
from typing import Any

from ...contracts.datasources import BibRecord
from ...contracts.errors import ApiError
from ...kernel.db import jdump, row, tx, utcnow
from ...kernel.events import emit

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def records_hash(records: list[BibRecord]) -> str:
    return hashlib.sha256(jdump([asdict(r) for r in records]).encode()).hexdigest()


def subject_hash(conn: sqlite3.Connection, subject_id: str) -> str:
    """The subject IS the content hash of the selection (resolver for approval subject knowledge_import)."""
    if not HEX64.match(subject_id):
        raise ApiError(422, "bad_subject", "knowledge_import subject id must be the sha256 of the selected records")
    return subject_id


def consume_approval(conn: sqlite3.Connection, records: list[BibRecord], actor: str) -> str:
    h = records_hash(records)
    with tx(conn, join=True):  # a caller may record the use in the same transaction (deep research, Review #98)
        a = row(conn.execute("SELECT * FROM approvals WHERE subject_type='knowledge_import' AND subject_id=? AND "
                             "subject_hash=? AND revoked_at IS NULL AND consumed_at IS NULL AND expires_at>? "
                             "ORDER BY created_at DESC LIMIT 1", (h, h, utcnow())))
        if a is None:
            raise ApiError(409, "import_not_approved", "approve this exact selection first",
                           details={"subject_type": "knowledge_import", "subject_id": h})
        conn.execute("UPDATE approvals SET consumed_at=? WHERE id=?", (utcnow(), a["id"]))
        emit(conn, "import.approved_selection_used", actor=actor, entity_type="approval", entity_id=a["id"],
             data={"records": len(records)})
    return h


def record_from_dict(d: dict[str, Any]) -> BibRecord:
    d = dict(d)
    d["authors"] = tuple(d.get("authors") or ())
    d["identifiers"] = tuple(tuple(x) for x in d.get("identifiers") or ())
    return BibRecord(**d)
