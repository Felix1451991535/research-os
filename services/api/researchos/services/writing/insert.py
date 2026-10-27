"""Cited answers -> doc text with placeholders. Only verified sentences with evidence are carried over; each keeps
its `{{evidence:id}}` and the citekeys of its works, so the doc stays gate-clean."""

from __future__ import annotations

import sqlite3

from ...contracts.errors import ApiError, not_found
from ...kernel.db import jload, row
from ..literature import citekeys


def answer_markdown(conn: sqlite3.Connection, answer_id: str, heading: str | None = None) -> str:
    a = row(conn.execute("SELECT question, status, body FROM answers WHERE id=?", (answer_id,)))
    if a is None:
        raise not_found("answer")
    sents = [s for s in (jload(a["body"]) or {}).get("sentences") or [] if s.get("evidence_ids")]
    if a["status"] != "answered" or not sents:
        raise ApiError(409, "no_cited_sentences", "this answer has no verified, cited sentences to insert")
    lines = [f"## {heading or a['question']}", ""]
    for s in sents:
        lines.append(citekeys.cited_markdown(conn, s["text"], list(s["evidence_ids"])))
        lines.append("")
    return "\n".join(lines)
