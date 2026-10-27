"""K7 single validated write path for Evidence + verify-before-use (HARDENING #3, #4).

create(): an Evidence row exists only if the quote is found (after deterministic normalization) in the stored
page text of that exact document version; numbers in any extracted value must occur in the quote.
verify(): re-checks under a short write lock that the anchored text still matches (version + page digest +
offsets) before the evidence is shown to a model or cited.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Literal

from ...contracts.errors import ApiError
from ...kernel.db import Row, new_id, row, tx, utcnow
from ...kernel.events import emit
from .text import find_span, missing_numbers, norm

MIN_QUOTE = 8
KINDS = ("source_fact", "author_conclusion", "model_inference", "hypothesis")


class EvidenceRejected(ApiError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(422, code, message)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def create(conn: sqlite3.Connection, *, version_id: str, page_index: int, quote: str, created_by: str,
           project_id: str | None = None, kind: str = "source_fact", extracted_value: str | None = None) -> Row:
    with tx(conn, join=True):
        return create_in_tx(conn, version_id=version_id, page_index=page_index, quote=quote, created_by=created_by,
                            project_id=project_id, kind=kind, extracted_value=extracted_value)


def create_in_tx(conn: sqlite3.Connection, *, version_id: str, page_index: int, quote: str, created_by: str,
           project_id: str | None = None, kind: str = "source_fact", extracted_value: str | None = None) -> Row:
    if kind not in KINDS:
        raise EvidenceRejected("bad_kind", f"kind must be one of {KINDS}")
    if len(norm(quote)) < MIN_QUOTE:
        raise EvidenceRejected("quote_too_short", f"quote must have at least {MIN_QUOTE} characters")
    if extracted_value is not None:
        miss = missing_numbers(extracted_value, quote)
        if miss:
            raise EvidenceRejected("number_not_in_quote", f"numbers {miss} do not occur in the quote")
    page = row(conn.execute("SELECT p.*, v.parser_version FROM pages p JOIN document_versions v "
                            "ON v.version_id=p.version_id WHERE p.version_id=? AND p.page_index=?",
                            (version_id, page_index)))
    if page is None:
        raise EvidenceRejected("page_not_found", "no parsed page with that index for this document version")
    span = find_span(page["text"], quote)
    if span is None:
        emit(conn, "evidence.rejected", actor=created_by, entity_type="document_version", entity_id=version_id,
             data={"reason": "quote_not_found", "page": page_index})
        raise EvidenceRejected("quote_not_found", "the quote does not occur on that page of the source")
    a, b = span
    chunk = row(conn.execute("SELECT chunk_id FROM chunks WHERE version_id=? AND page_index=? AND char_start<=? "
                             "AND char_end>? ORDER BY char_start DESC LIMIT 1", (version_id, page_index, a, a)))
    trust = "machine_checked" if page["quality"] == "ok" else "unverified"
    eid = new_id("evd")
    exact = page["text"][a:b]
    conn.execute("INSERT INTO evidence(id,project_id,version_id,chunk_id,page_index,char_start,char_end,quote,"
                 "quote_sha256,page_text_sha256,parser_version,kind,trust,created_by,created_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (eid, project_id, version_id, chunk["chunk_id"] if chunk else None, page_index, a, b, exact,
                  _sha(exact), page["text_sha256"], page["parser_version"], kind, trust, created_by, utcnow()))
    emit(conn, "evidence.created", actor=created_by, entity_type="evidence", entity_id=eid, project_id=project_id,
         data={"version_id": version_id, "page": page_index, "trust": trust})
    return dict(row(conn.execute("SELECT * FROM evidence WHERE id=?", (eid,))) or {})

VerifyStatus = Literal["ok", "stale", "missing", "superseded"]


@dataclass(frozen=True)
class Verification:
    status: VerifyStatus
    evidence: Row | None
    detail: str = ""


def verify(conn: sqlite3.Connection, evidence_id: str) -> Verification:
    """Re-verify before use. stale = page text changed or offsets no longer hold the quote (refuse to cite);
    superseded = still valid on its own version but a newer parse/version of the work exists (cite with label)."""
    with tx(conn, join=True):
        e = row(conn.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)))
        if e is None:
            return Verification("missing", None, "no such evidence")
        page = row(conn.execute("SELECT text, text_sha256 FROM pages WHERE version_id=? AND page_index=?",
                                (e["version_id"], e["page_index"])))
        if page is None:
            return Verification("stale", e, "source page no longer present")
        if page["text_sha256"] != e["page_text_sha256"] or _sha(page["text"][e["char_start"]:e["char_end"]]) != \
                e["quote_sha256"]:
            return Verification("stale", e, "source text changed since the quote was verified")
        newer = conn.execute(
            "SELECT 1 FROM document_versions v2 JOIN document_versions v1 ON v1.work_id=v2.work_id "
            "WHERE v1.version_id=? AND v2.version_id!=v1.version_id AND v2.created_at>v1.created_at", (e["version_id"],)
        ).fetchone()
        return Verification("superseded" if newer else "ok", e)


def verify_chunk(conn: sqlite3.Connection, chunk_id: str) -> bool:
    """Passage check right before it is sent to a model: chunk text must equal the current page slice."""
    r = row(conn.execute("SELECT c.text_sha256, c.char_start, c.char_end, p.text FROM chunks c JOIN pages p "
                         "ON p.version_id=c.version_id AND p.page_index=c.page_index WHERE c.chunk_id=?", (chunk_id,)))
    return bool(r) and _sha(r["text"][r["char_start"]:r["char_end"]]) == r["text_sha256"]  # type: ignore[index]
