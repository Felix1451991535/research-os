"""Title-based identification (ADR-233) for imported PDFs that carry no DOI / arXiv id: the title read from page 1
is searched on the configured sources (through discovery, so the egress gate applies), candidates whose title is
close enough are stored as SUGGESTIONS, and the document is linked to a work only when the owner confirms one.
Nothing is matched silently: an unconfirmed document stays unidentified (library `_unsorted`)."""

from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...kernel.db import jload, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from ..knowledge.writer import DbKnowledgeWriter
from . import candidates, discovery

MIN_SCORE = 0.85
MAX_SUGGESTIONS = 3
_NOT_A_TITLE = re.compile(r"(?i)\b(doi|https?://|www\.|arxiv|journal|vol\.|volume|issn|copyright|©|received|"
                          r"accepted|preprint|page \d)")


def title_guess(page_text: str) -> str | None:
    """The first line of page 1 that looks like a title (4-40 words, 15-300 chars, no header/footer markers)."""
    for line in (ln.strip() for ln in page_text.splitlines()[:15]):
        words = line.split()
        if 4 <= len(words) <= 40 and 15 <= len(line) <= 300 and not _NOT_A_TITLE.search(line):
            return line
    return None


def _norm(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).casefold()
    return " ".join(re.findall(r"[^\W_]+", t))


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def suggest(conn: sqlite3.Connection, ctx: discovery.Ctx, version_id: str, title: str, sources: list[str]
            ) -> list[dict[str, Any]]:
    """Search `title`, store the best close matches as suggestions for the document version; returns them."""
    if not sources:
        return []
    res = discovery.search(conn, ctx, title, sources, page_size=5)
    scored = sorted(((similarity(title, c["record"]["title"]), c) for c in res["candidates"]),
                    key=lambda sc: -sc[0])
    keep = [(s, c) for s, c in scored if s >= MIN_SCORE][:MAX_SUGGESTIONS]
    now = utcnow()
    with tx(conn):
        for s, c in keep:
            conn.execute("INSERT OR IGNORE INTO doc_identifications(id, version_id, candidate_id, title_guess, score, "
                         "created_at) VALUES (?,?,?,?,?,?)", (new_id("did"), version_id, c["id"], title, round(s, 3),
                                                              now))
    return pending(conn, version_id)


def pending(conn: sqlite3.Connection, version_id: str | None = None) -> list[dict[str, Any]]:
    """Open suggestions (the owner's review queue), best first per document, plus confirmed ones whose linking
    never finished (`status` confirmed: confirm again to finish it, Review #99)."""
    where, args = ("AND i.version_id=?", (version_id,)) if version_id else ("", ())
    out = rows(conn.execute(
        "SELECT i.id, i.version_id, i.candidate_id, i.title_guess, i.score, i.status, i.created_at, "
        "v.original_name, c.record FROM doc_identifications i JOIN document_versions v ON v.version_id=i.version_id "
        f"JOIN lit_candidates c ON c.id=i.candidate_id WHERE (i.status='suggested' OR (i.status='confirmed' AND "
        f"i.linked_at IS NULL)) {where} "
        "ORDER BY i.version_id, i.score DESC", args))
    return [{**{k: v for k, v in r.items() if k != "record"}, "record": jload(r["record"])} for r in out]


def _decide(conn: sqlite3.Connection, ident_id: str, actor: str, status: str) -> dict[str, Any]:
    """Atomically take the decision for `ident_id` (one write transaction: status check + update), so concurrent
    confirms / rejects of the same document are serialised (Review #94). A confirm also rejects every other open
    suggestion for the document in the same transaction, and is refused while another suggestion for it is
    confirmed: at most one confirmed suggestion per document. Confirming a confirmed suggestion whose linking
    never finished (`linked_at` NULL: the process ended in between, Review #99) returns its decision unchanged so
    the caller finishes the (idempotent) linking. Every confirm takes a new attempt id on the suggestion, so a
    retry takes the linking over from an earlier, overlapping attempt (Review #101); `_undo` acts only for the
    attempt that owns it."""
    attempt = new_id("att")
    with tx(conn):
        r = row(conn.execute("SELECT * FROM doc_identifications WHERE id=?", (ident_id,)))
        if r is None:
            raise not_found("identification suggestion")
        if status == "confirmed" and r["status"] == "confirmed" and r["linked_at"] is None:
            # a confirm without a decision id (from before core 0014; 0017 gives those one) gets its own, so a
            # failed retry can still re-open it (Review #102)
            decision = r["decision_id"] or new_id("dec")
            conn.execute("UPDATE doc_identifications SET attempt_id=?, decision_id=? WHERE id=?",
                         (attempt, decision, ident_id))
            return {**r, "decision_id": decision, "attempt_id": attempt}
        if r["status"] != "suggested" or (status == "confirmed" and row(conn.execute(
                "SELECT 1 FROM doc_identifications WHERE version_id=? AND status='confirmed'", (r["version_id"],)))):
            raise ApiError(409, "already_decided", "this suggestion was already confirmed or rejected")
        decision = _set_decision(conn, r["version_id"], ident_id, status, actor,
                                 attempt if status == "confirmed" else None)
    return {**r, "decision_id": decision, "attempt_id": attempt}


def _set_decision(conn: sqlite3.Connection, version_id: str, ident_id: str, status: str, actor: str,
                  attempt: str | None) -> str:
    """In the caller's transaction: decide `ident_id` under a new decision id; a confirm also rejects the document's
    other open suggestions under the same id. Returns the decision id."""
    now, decision = utcnow(), new_id("dec")
    conn.execute("UPDATE doc_identifications SET status=?, decided_by=?, decided_at=?, decision_id=?, attempt_id=? "
                 "WHERE id=?", (status, actor, now, decision, attempt, ident_id))
    if status == "confirmed":
        conn.execute("UPDATE doc_identifications SET status='rejected', decided_by=?, decided_at=?, decision_id=? "
                     "WHERE version_id=? AND status='suggested'", (actor, now, decision, version_id))
    return decision


def _undo(conn: sqlite3.Connection, ident_id: str, attempt: str) -> None:
    """Re-open the suggestions a failed confirm decided (linking raised): the owner can decide again. Only those of
    this decision (its unique id), never another decision taken in the same millisecond (Review #100); only while
    this attempt still owns the linking (a retry that took it over may still finish it, Review #101); never once
    the linking has finished."""
    with tx(conn):
        r = row(conn.execute("SELECT decision_id FROM doc_identifications WHERE id=? AND attempt_id=? AND "
                             "status='confirmed' AND linked_at IS NULL", (ident_id, attempt)))
        if r is not None:
            conn.execute("UPDATE doc_identifications SET status='suggested', decided_by=NULL, decided_at=NULL, "
                         "decision_id=NULL, attempt_id=NULL WHERE decision_id=?", (r["decision_id"],))


def _finish(conn: sqlite3.Connection, r: dict[str, Any], actor: str, work_id: str) -> None:
    """Mark the suggestion linked and emit `library.identified`, once (an overlapping attempt may have finished
    first). If an overlapping attempt that owned the linking failed meanwhile and re-opened the suggestions, the
    decision is taken again (siblings rejected) - unless the owner has decided the document otherwise in between
    (409). Runs as the guard of the relink transaction (Review #103), so a 409 leaves the document's link as the
    other decision made it; joins that transaction, or opens its own after an idempotent replay."""
    with tx(conn, join=True):
        cur = row(conn.execute("SELECT status, linked_at FROM doc_identifications WHERE id=?", (r["id"],)))
        assert cur is not None
        if cur["linked_at"] is not None:
            return
        if cur["status"] != "confirmed":
            if cur["status"] != "suggested" or row(conn.execute(
                    "SELECT 1 FROM doc_identifications WHERE version_id=? AND status='confirmed'", (r["version_id"],))):
                raise ApiError(409, "already_decided", "this suggestion was already confirmed or rejected")
            _set_decision(conn, r["version_id"], r["id"], "confirmed", actor, r["attempt_id"])
        conn.execute("UPDATE doc_identifications SET linked_at=? WHERE id=?", (utcnow(), r["id"]))
        emit(conn, "library.identified", actor=actor, entity_type="document_version", entity_id=r["version_id"],
             data={"work_id": work_id, "by": "title", "score": r["score"]})


def confirm(conn: sqlite3.Connection, writer: DbKnowledgeWriter, ident_id: str, data_dir: Path) -> str:
    """Owner action: link the document version to the candidate's work (attach_original re-links the same stored
    bytes); the other open suggestions for the same document are rejected by the same decision. The decision is
    taken first and atomically (`_decide`), so a concurrent confirm / reject gets 409 and the document is relinked
    by exactly one confirm. The suggestion is final once `linked_at` is set with the `library.identified` event; until
    then a confirm again (e.g. after the process ended mid-way) finishes the linking, whose writes are idempotent per
    suggestion; overlapping attempts are fenced by their attempt id (Review #101), and the decision is checked and
    the suggestion marked linked in the same transaction as the relink, so an attempt whose decision was re-opened
    and taken by another confirm cannot relink the document (Review #103). Returns the work id."""
    r = _decide(conn, ident_id, writer.actor, "confirmed")
    try:
        v = row(conn.execute("SELECT original_name, stored_path FROM document_versions WHERE version_id=?",
                             (r["version_id"],)))
        assert v is not None  # FK
        rec = candidates.record_of(candidates.get(conn, r["candidate_id"]))
        work_id, _ = writer.upsert_work(rec, idempotency_key=f"identify:{ident_id}")
        writer.attach_original(work_id, (data_dir / str(v["stored_path"])).read_bytes(), str(v["original_name"]),
                               license=None, idempotency_key=f"identify:pdf:{ident_id}",
                               guard=lambda: _finish(conn, r, writer.actor, work_id))
    except BaseException:
        _undo(conn, ident_id, r["attempt_id"])
        raise
    _finish(conn, r, writer.actor, work_id)  # a no-op once linked; finishes a linking replayed from before #103
    return work_id


def reject(conn: sqlite3.Connection, ident_id: str, actor: str) -> None:
    _decide(conn, ident_id, actor, "rejected")
