"""Cited cards (paper card, methods checklist: ADR-180; topic feasibility: ADR-181): a fixed doc whose fields are
cited QA answers - restricted to one work's full text (scope "work") or over the project library (scope "project") -
proposed through suggestion mode (the owner accepts), plus deterministic tail sections. A field without verified
evidence stays "insufficient material". Nothing is written from abstracts or model memory. Resumable: an answer saved
before an interruption is reused; a field whose paid call may have run without a saved answer stops for inspection.
`subject` holds the scope inputs ({"work_id"} or e.g. {"topic"}); questions are formatted with it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...kernel.db import jload, row, tx
from ..content import docs
from ..gateway.gateway import Gateway
from ..literature import citekeys
from ..research import qa
from . import insert, suggestions

Tail = Callable[[sqlite3.Connection, dict[str, Any], dict[str, Any]], list[tuple[str, str, str]]]  # key, text, why


@dataclass(frozen=True)
class CardSpec:
    kind: str  # doc kind
    title_prefix: str
    fields: tuple[tuple[str, str, str], ...]  # (key, heading, question)
    tail_sections: tuple[tuple[str, str], ...] = ()  # (key, heading) filled deterministically by `tail`
    owner_section: str | None = None  # left for the owner, never filled
    tail: Tail = field(default=lambda conn, subject, done: [])
    scope: str = "work"  # work | project


def marker(key: str) -> str:
    return f"_(pending: {key})_"


def skeleton(conn: sqlite3.Connection, spec: CardSpec, subject: dict[str, Any]) -> str:
    head = (f"{spec.title_prefix} for {{{{cite:{citekeys.assign(conn, subject['work_id'])}}}}}" if spec.scope == "work"
            else f"{spec.title_prefix}: {subject['topic']}")
    lines = [head, ""]
    for k, heading, _ in spec.fields:
        lines += [f"## {heading}", "", marker(k), ""]
    for k, heading in spec.tail_sections:
        lines += [f"## {heading}", "", marker(k), ""]
    if spec.owner_section:
        lines += [f"## {spec.owner_section}", "", "_(owner)_", ""]
    return "\n".join(lines)


def _title(conn: sqlite3.Connection, spec: CardSpec, subject: dict[str, Any]) -> str:
    if spec.scope != "work":
        return f"{spec.title_prefix}: {subject['topic']}"
    work_id = subject["work_id"]
    w = row(conn.execute("SELECT title FROM works WHERE id=?", (work_id,)))
    if w is None:
        raise not_found("work")
    if row(conn.execute("SELECT 1 FROM document_versions WHERE work_id=? AND parse_state IN ('parsed','partial')",
                        (work_id,))) is None:
        raise ApiError(409, "no_full_text", f"a {spec.title_prefix.lower()} needs a parsed full text of this work")
    return f"{spec.title_prefix}: {w['title']}"


def _start(conn: sqlite3.Connection, doc_kinds: Any, spec: CardSpec, subject: dict[str, Any], project_id: str,
           actor: str, progress: dict[str, Any], checkpoint: Callable[[], None]) -> None:
    title = _title(conn, spec, subject)
    if "doc" not in progress:
        with tx(conn):
            progress["doc"] = docs.create_in_tx(conn, doc_kinds, project_id, spec.kind, title,
                                                skeleton(conn, spec, subject), actor)
            progress["fields"] = {}
            checkpoint()


def _field(conn: sqlite3.Connection, gw: Gateway, spec_field: tuple[str, str, str], doc: dict[str, Any],
           subject: dict[str, Any], project_id: str, actor: str, progress: dict[str, Any],
           checkpoint: Callable[[], None]) -> None:
    k, heading, question = spec_field
    trace = f"card:{doc['id']}:{k}"
    a = row(conn.execute("SELECT id,status,body FROM answers WHERE trace_id=? ORDER BY created_at LIMIT 1", (trace,)))
    if a is None:
        if progress.get("pending") == k:
            # Provider completion cannot be inferred after termination. Never silently repeat paid work.
            raise ApiError(409, "operation_uncertain", "Card field has no saved answer after interruption; "
                           "inspect the gateway call before starting a new intent.", details={"trace_id": trace})
        progress["pending"] = k
        checkpoint()
        a = qa.answer(conn, gw, qa.QAContext(question.format_map(subject), project_id, actor, trace,
                                             work_id=subject.get("work_id")))
        a = row(conn.execute("SELECT id,status,body FROM answers WHERE id=?", (a["id"],))) or a
    try:
        text = insert.answer_markdown(conn, a["id"], heading).split("\n", 2)[2].strip()
    except ApiError:
        text = f"Insufficient material: no verified, cited statement answers this ({a['status']})."
    with tx(conn):
        s = suggestions.propose(conn, doc["id"], op="replace", anchor=marker(k), text=text, actor=actor,
                                rationale=f"card field '{k}' from cited answer", source={"answer_id": a["id"]})
        kinds: dict[str, int] = {}
        for sentence in (jload(a.get("body")) or {}).get("sentences", []):
            kinds[str(sentence.get("kind"))] = kinds.get(str(sentence.get("kind")), 0) + 1
        progress["fields"][k] = {"answer_id": a["id"], "status": a["status"], "suggestion_id": s["id"],
                                 "cited": not text.startswith("Insufficient material"), "kinds": kinds}
        progress.pop("pending", None)
        checkpoint()


def create(conn: sqlite3.Connection, gw: Gateway, doc_kinds: Any, spec: CardSpec, subject: dict[str, Any], *,
           project_id: str, actor: str, progress: dict[str, Any] | None = None,
           checkpoint: Callable[[], None] = lambda: None) -> dict[str, Any]:
    progress = progress if progress is not None else {}
    _start(conn, doc_kinds, spec, subject, project_id, actor, progress, checkpoint)
    doc = progress["doc"]
    for f in spec.fields:
        if f[0] not in progress["fields"]:
            _field(conn, gw, f, doc, subject, project_id, actor, progress, checkpoint)
    for k, text, rationale in spec.tail(conn, subject, progress["fields"]):
        if k not in progress["fields"]:
            with tx(conn):
                s = suggestions.propose(conn, doc["id"], op="replace", anchor=marker(k), text=text, actor=actor,
                                        rationale=rationale)
                progress["fields"][k] = {"suggestion_id": s["id"]}
                checkpoint()
    return {"doc": doc, "fields": progress["fields"]}
