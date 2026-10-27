"""Persist a ParseResult: pages + deterministic chunk ids + FTS (via triggers) + identifiers/title; also used to
record parse failures with a recovery action. Same-DOI documents become versions of one Work."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from ...contracts.knowledge import PageQuality, ParseError, ParseResult
from ...kernel.db import row, tx
from ...kernel.events import emit
from .metadata import find_identifiers, guess_title


def chunk_id(version_id: str, parser_version: str, page: int, offset: int) -> str:
    return "chk_" + hashlib.sha256(f"{version_id}|{parser_version}|{page}|{offset}".encode()).hexdigest()[:24]


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _state(res: ParseResult) -> str:
    q = [p.quality for p in res.pages]
    if q and all(x is PageQuality.EMPTY for x in q):
        return "needs_ocr"
    return "parsed" if all(x is PageQuality.OK for x in q) else "partial"


def record_failure(conn: sqlite3.Connection, version_id: str, err: ParseError, actor: str) -> None:
    with tx(conn):
        conn.execute("UPDATE document_versions SET parse_state=?, parse_error=?, recovery_action=? WHERE version_id=?",
                     ("unsupported" if err.code == "unsupported" else "failed", f"{err.code}: {err}", err.recovery,
                      version_id))
        emit(conn, "document.parse_failed", actor=actor, entity_type="document_version", entity_id=version_id,
             data={"code": err.code})


def _link_identifiers(conn: sqlite3.Connection, version_id: str, work_id: str, text: str) -> str:
    """Same DOI/arXiv id = same Work (hard identifier merge). Returns the work id the version belongs to."""
    for scheme, value in find_identifiers(text):
        other = row(conn.execute("SELECT work_id FROM identifiers WHERE scheme=? AND value=?", (scheme, value)))
        if other and other["work_id"] != work_id:
            conn.execute("UPDATE document_versions SET work_id=? WHERE version_id=?", (other["work_id"], version_id))
            conn.execute("UPDATE identifiers SET work_id=? WHERE work_id=?", (other["work_id"], work_id))
            conn.execute("DELETE FROM works WHERE id=? AND NOT EXISTS (SELECT 1 FROM document_versions "
                         "WHERE work_id=?)", (work_id, work_id))
            return str(other["work_id"])
        if not other:
            conn.execute("INSERT INTO identifiers(work_id,scheme,value,source) VALUES (?,?,?,?)",
                         (work_id, scheme, value, "pdf_text"))
    return work_id


def store_result(conn: sqlite3.Connection, version_id: str, res: ParseResult, actor: str) -> str:
    with tx(conn):
        v = row(conn.execute("SELECT * FROM document_versions WHERE version_id=?", (version_id,)))
        if v is None:
            raise LookupError(version_id)
        for p in res.pages:
            conn.execute("INSERT OR REPLACE INTO pages(version_id,page_index,page_label,width,height,rotation,text,"
                         "text_sha256,char_count,quality,flags) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (version_id, p.page_index, p.page_label, p.width, p.height, p.rotation, p.text,
                          _sha(p.text), len(p.text), p.quality.value, json.dumps(list(p.flags))))
            for seq, c in enumerate(p.chunks):
                conn.execute("INSERT OR IGNORE INTO chunks(chunk_id,version_id,parser_version,page_index,char_start,"
                             "char_end,bbox,section_role,chunk_kind,text,text_sha256,quality,seq) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (chunk_id(version_id, res.parser_version, p.page_index, c.char_start), version_id,
                              res.parser_version, p.page_index, c.char_start, c.char_end,
                              json.dumps(c.bbox) if c.bbox else None, c.section_role, c.chunk_kind, c.text,
                              _sha(c.text), p.quality.value, seq))
        first = res.pages[0].text if res.pages else ""
        work_id = _link_identifiers(conn, version_id, v["work_id"], first + "\n" + "\n".join(res.metadata.values()))
        title = guess_title(first, res.metadata.get("title"))
        if title:
            conn.execute("UPDATE works SET title=?, updated_at=datetime('now') WHERE id=? AND title=?",
                         (title, work_id, v["original_name"].rsplit(".", 1)[0][:300]))
        state = _state(res)
        recovery = "pages without text need OCR (planned plugin)" if state == "needs_ocr" else (
            "some pages need proofreading; open the source page to check" if state == "partial" else None)
        conn.execute("UPDATE document_versions SET parse_state=?, parser_version=?, page_count=?, parse_error=NULL, "
                     "recovery_action=? WHERE version_id=?", (state, res.parser_version, len(res.pages), recovery,
                                                              version_id))
        emit(conn, "document.parsed", actor=actor, entity_type="document_version", entity_id=version_id,
             data={"state": state, "pages": len(res.pages), "parser_version": res.parser_version})
    return state
