"""Reading aids (research-method feature 2): figure/table captions first, then the authors' main-point signal
sentences. Deterministic (no model): captions come from the parser's `chunk_kind='caption'`; signal sentences are
matched by a versioned cue list in body chunks (references excluded). Every item is stored through the single
validated Evidence write path (quote located on the page -> page + offsets + hashes); captions as `source_fact`,
signal sentences as `author_conclusion`. The cue is a K1 relation `reading_cue` on the Evidence row."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ...kernel.db import jdump, row, rows, tx, utcnow
from . import evidence
from .text import find_span

CUES_VERSION = "reading-cues@1"
ACTOR = f"system:{CUES_VERSION}"
CUES: tuple[tuple[str, re.Pattern[str]], ...] = tuple((name, re.compile(rx, re.IGNORECASE)) for name, rx in (
    ("we_propose", r"\bwe (?:propose|present|introduce|develop)\b|我们提出"),
    ("contrast_prior", r"\b(?:in contrast (?:with|to)|unlike|compared (?:with|to)) (?:the )?"
                       r"(?:previous|prior|earlier|existing) (?:work|studies|research|methods|approaches)\b|"
                       r"与(?:以往|既往|现有)(?:研究|工作|方法)不同"),
    ("data_suggest", r"\b(?:the|our|these) (?:data|results|findings) (?:suggest|indicate|show|demonstrate)\b|"
                     r"(?:结果|数据)(?:表明|提示|显示)"),
    ("we_find", r"\bwe (?:find|found|show|demonstrate) that\b"),
    ("contribution", r"\b(?:our|the main|the key) contributions? (?:is|are|include)\b"),
    ("conclude", r"\b(?:we conclude|in conclusion|taken together)\b|综上所述"),
    ("limitation", r"\b(?:a|one|the main) limitation\b|\blimitations? of (?:this|our) (?:study|work)\b"),
))
_SENT = re.compile(r"(?<=[.!?。！？])\s+")
SKIP_ROLES = ("references",)


def _sentences(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [s.strip() for s in _SENT.split(flat) if len(s.strip()) >= evidence.MIN_QUOTE]


def _caption(text: str) -> str:
    """Caption label + its first sentence (a bare 'Figure 1.' takes the next sentence too)."""
    ss = _sentences(text) or [" ".join(text.split())]
    first = ss[0]
    if len(first) < 15 and len(ss) > 1:
        first = f"{first} {ss[1]}"
    return first[:400]


def _link(conn: sqlite3.Connection, evidence_id: str, cue: str) -> None:
    conn.execute("INSERT OR IGNORE INTO relations(src,type,dst,attrs,created_at) VALUES (?,?,?,?,?)",
                 (evidence_id, "reading_cue", f"cue:{cue}", jdump({"version": CUES_VERSION}), utcnow()))

def _store(conn: sqlite3.Connection, c: dict[str, Any], quote: str, kind: str, cue: str,
           project_id: str | None) -> dict[str, Any] | None:
    with tx(conn):
        page = row(conn.execute("SELECT text FROM pages WHERE version_id=? AND page_index=?",
                                (c["version_id"], c["page_index"])))
        span = find_span(page["text"], quote) if page else None
        if span is None:
            return None  # not locatable on the page (e.g. hyphenation): never stored unverified
        # Serialize deduplication, validated creation and cue repair under one write lock.
        dup = row(conn.execute("SELECT id FROM evidence WHERE version_id=? AND page_index=? AND char_start=? AND "
                               "char_end=? AND kind=? AND created_by=?",
                               (c["version_id"], c["page_index"], span[0], span[1], kind, ACTOR)))
        if dup:
            _link(conn, dup["id"], cue)
            return None
        try:
            e = evidence.create_in_tx(conn, version_id=c["version_id"], page_index=c["page_index"], quote=quote,
                                created_by=ACTOR, project_id=project_id, kind=kind)
        except evidence.EvidenceRejected:
            return None
        _link(conn, e["id"], cue)
        return e

def extract(conn: sqlite3.Connection, version_id: str, project_id: str | None = None) -> dict[str, int]:
    """Idempotent. Returns counts of NEW captions / signal sentences stored."""
    chunks = rows(conn.execute(
        "SELECT c.* FROM chunks c JOIN document_versions v ON v.version_id=c.version_id "
        "WHERE c.version_id=? AND c.parser_version=v.parser_version ORDER BY c.page_index, c.seq", (version_id,)))
    out = {"captions": 0, "signals": 0}
    for c in (c for c in chunks if c["chunk_kind"] == "caption"):
        if _store(conn, c, _caption(c["text"]), "source_fact", "caption", project_id):
            out["captions"] += 1
    for c in (c for c in chunks if c["chunk_kind"] == "text" and c["section_role"] not in SKIP_ROLES):
        for s in _sentences(c["text"]):
            cue = next((name for name, rx in CUES if rx.search(s)), None)
            if cue and _store(conn, c, s, "author_conclusion", cue, project_id):
                out["signals"] += 1
    return out


def listing(conn: sqlite3.Connection, version_id: str) -> dict[str, Any]:
    """Captions first, then signal sentences; each with page, offsets, quote, cue, trust."""
    items = rows(conn.execute(
        "SELECT e.id, e.page_index, e.char_start, e.char_end, e.quote, e.kind, e.trust, r.dst AS cue FROM evidence e "
        "JOIN relations r ON r.src=e.id AND r.type='reading_cue' WHERE e.version_id=? "
        "ORDER BY e.page_index, e.char_start", (version_id,)))
    for i in items:
        i["cue"] = i["cue"].split(":", 1)[1]
    return {"cues_version": CUES_VERSION, "captions": [i for i in items if i["cue"] == "caption"],
            "main_points": [i for i in items if i["cue"] != "caption"]}
