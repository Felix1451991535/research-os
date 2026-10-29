"""Cited cards (paper card, methods checklist: ADR-180; topic feasibility: ADR-181): a fixed doc whose fields are
cited QA answers - restricted to one work's full text (scope "work") or over the project library (scope "project") -
proposed through suggestion mode (the owner accepts), plus deterministic tail sections. A field without verified
evidence stays "insufficient material". Nothing is written from abstracts or model memory. Resumable: an answer saved
before an interruption is reused; a field whose paid call may have run without a saved answer stops for inspection.
`subject` holds the scope inputs ({"work_id"} or e.g. {"topic"}); questions are formatted with it.
Fixed text (title, headings, "insufficient material", the owner placeholder) comes from the core catalog in the card's
`language` (ADR-246; en default): headings are `card_<kind>_<key>`, the title prefix `card_<kind>_title`. Questions
are model prompts and stay English; answers are model output and are not translated.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...kernel.db import jload, row, tx
from ...sdk.texts import DEFAULT_LANGUAGE, Bound
from ..content import docs
from ..content.texts import CORE
from ..gateway.gateway import Gateway
from ..literature import citekeys
from ..research import qa
from . import insert, suggestions

Tail = Callable[[sqlite3.Connection, dict[str, Any], dict[str, Any], Bound],
                list[tuple[str, str, str]]]  # key, text, why


@dataclass(frozen=True)
class CardSpec:
    kind: str  # doc kind; catalog codes card_<kind>_title / _<key> / _owner
    fields: tuple[tuple[str, str], ...]  # (key, question)
    tail_sections: tuple[str, ...] = ()  # keys filled deterministically by `tail`
    owner_section: bool = False  # a `card_<kind>_owner` section left for the owner, never filled
    tail: Tail = field(default=lambda conn, subject, done, T: [])
    scope: str = "work"  # work | project

    def heading(self, T: Bound, key: str) -> str:
        return T(f"card_{self.kind}_{key}")


def marker(key: str) -> str:
    return f"_(pending: {key})_"  # i18n-exempt: suggestion anchor (protocol), replaced before the owner reads


def skeleton(conn: sqlite3.Connection, spec: CardSpec, subject: dict[str, Any], T: Bound) -> str:
    prefix = spec.heading(T, "title")
    head = (T("card_title_work", prefix=prefix, cite=f"{{{{cite:{citekeys.assign(conn, subject['work_id'])}}}}}")
            if spec.scope == "work" else T("card_title", prefix=prefix, subject=subject["topic"]))
    lines = [head, ""]
    for k in [*(f[0] for f in spec.fields), *spec.tail_sections]:
        lines += [f"## {spec.heading(T, k)}", "", marker(k), ""]
    if spec.owner_section:
        lines += [f"## {spec.heading(T, 'owner')}", "", T("card_owner_placeholder"), ""]
    return "\n".join(lines)


def _title(conn: sqlite3.Connection, spec: CardSpec, subject: dict[str, Any], T: Bound) -> str:
    prefix = spec.heading(T, "title")
    if spec.scope != "work":
        return T("card_title", prefix=prefix, subject=subject["topic"])
    work_id = subject["work_id"]
    w = row(conn.execute("SELECT title FROM works WHERE id=?", (work_id,)))
    if w is None:
        raise not_found("work")
    if row(conn.execute("SELECT 1 FROM document_versions WHERE work_id=? AND parse_state IN ('parsed','partial')",
                        (work_id,))) is None:
        raise ApiError(409, "no_full_text", f"a {spec.kind.replace('_', ' ')} needs a parsed full text of this work")
    return T("card_title", prefix=prefix, subject=w["title"])


def _start(conn: sqlite3.Connection, doc_kinds: Any, spec: CardSpec, subject: dict[str, Any], project_id: str,
           actor: str, progress: dict[str, Any], checkpoint: Callable[[], None], T: Bound) -> None:
    title = _title(conn, spec, subject, T)
    if "doc" not in progress:
        with tx(conn):
            progress["doc"] = docs.create_in_tx(conn, doc_kinds, project_id, spec.kind, title,
                                                skeleton(conn, spec, subject, T), actor)
            progress["fields"] = {}
            checkpoint()


def _field(conn: sqlite3.Connection, gw: Gateway, spec_field: tuple[str, str], doc: dict[str, Any],
           subject: dict[str, Any], project_id: str, actor: str, progress: dict[str, Any],
           checkpoint: Callable[[], None], T: Bound) -> None:
    k, question = spec_field
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
        text, cited = insert.answer_markdown(conn, a["id"], k).split("\n", 2)[2].strip(), True
    except ApiError:
        text, cited = T("card_insufficient", status=a["status"]), False
    with tx(conn):
        s = suggestions.propose(conn, doc["id"], op="replace", anchor=marker(k), text=text, actor=actor,
                                rationale=f"card field '{k}' from cited answer", source={"answer_id": a["id"]})
        kinds: dict[str, int] = {}
        for sentence in (jload(a.get("body")) or {}).get("sentences", []):
            kinds[str(sentence.get("kind"))] = kinds.get(str(sentence.get("kind")), 0) + 1
        progress["fields"][k] = {"answer_id": a["id"], "status": a["status"], "suggestion_id": s["id"],
                                 "cited": cited, "kinds": kinds}
        progress.pop("pending", None)
        checkpoint()


def create(conn: sqlite3.Connection, gw: Gateway, doc_kinds: Any, spec: CardSpec, subject: dict[str, Any], *,
           project_id: str, actor: str, progress: dict[str, Any] | None = None,
           checkpoint: Callable[[], None] = lambda: None, language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    progress = progress if progress is not None else {}
    T = CORE.lang(language)
    _start(conn, doc_kinds, spec, subject, project_id, actor, progress, checkpoint, T)
    doc = progress["doc"]
    for f in spec.fields:
        if f[0] not in progress["fields"]:
            _field(conn, gw, f, doc, subject, project_id, actor, progress, checkpoint, T)
    for k, text, rationale in spec.tail(conn, subject, progress["fields"], T):
        if k not in progress["fields"]:
            with tx(conn):
                s = suggestions.propose(conn, doc["id"], op="replace", anchor=marker(k), text=text, actor=actor,
                                        rationale=rationale)
                progress["fields"][k] = {"suggestion_id": s["id"]}
                checkpoint()
    return {"doc": doc, "fields": progress["fields"]}
