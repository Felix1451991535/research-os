"""Saved searches (query or author watch) with catch-up schedules, new-paper alerts, rule auto-screening and
opt-in metadata/PDF auto-import; weekly library retraction checks. Runs are K2 tasks enqueued by `trigger`."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import jsonschema

from ...contracts.errors import ApiError, not_found
from ...kernel.db import Row, jdump, jload, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from ..knowledge.writer import DbKnowledgeWriter
from ..orchestration import tasks as task_svc
from ..orchestration.registry import KindRegistry
from . import discovery, importing, oa, screening

CADENCE = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
SCHEMA = {"type": "object", "required": ["name", "kind", "query", "sources", "cadence"], "additionalProperties": False,
          "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 200},
                         "kind": {"enum": ["query", "author"]},
                         "query": {"type": "object", "additionalProperties": False,
                                   "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 500},
                                                  "author": {"type": "string", "minLength": 2, "maxLength": 200},
                                                  "filters": {"type": "object"}}},
                         "sources": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                         "cadence": {"enum": ["manual", "daily", "weekly"]},
                         "review_id": {"type": ["string", "null"]}, "project_id": {"type": ["string", "null"]},
                         "auto_import": {"type": "boolean"}, "pdf_download": {"type": "boolean"}}}
RETRACTION_EVERY = timedelta(days=7)


def _iso(t: datetime) -> str:
    """Same format as kernel.db.utcnow (UTC, milliseconds, +00:00) so stored times compare as strings."""
    return t.astimezone(UTC).isoformat(timespec="milliseconds")


def create(conn: sqlite3.Connection, data: dict[str, Any], actor: str, sources_known: list[str]) -> Row:
    try:
        jsonschema.validate(data, SCHEMA)
    except jsonschema.ValidationError as e:
        raise ApiError(422, "invalid_saved_search", e.message) from None
    q = data["query"]
    if (data["kind"] == "author") != ("author" in q) or (data["kind"] == "query") != ("text" in q):
        raise ApiError(422, "invalid_saved_search", "kind=query needs query.text; kind=author needs query.author")
    unknown = [s for s in data["sources"] if s not in sources_known]
    if unknown:
        raise ApiError(422, "unknown_source", f"unknown sources {unknown}")
    if data.get("review_id"):
        screening.get(conn, data["review_id"])
    sid, now = new_id("ss"), utcnow()
    with tx(conn):
        conn.execute("INSERT INTO saved_searches(id,project_id,name,kind,query,sources,cadence,review_id,auto_import,"
                     "pdf_download,enabled,next_run_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                     (sid, data.get("project_id"), data["name"], data["kind"], jdump(q), jdump(data["sources"]),
                      data["cadence"], data.get("review_id"), int(data.get("auto_import", False)),
                      int(data.get("pdf_download", False)), None if data["cadence"] == "manual" else now, now, now))
        emit(conn, "saved_search.created", actor=actor, entity_type="saved_search", entity_id=sid,
             project_id=data.get("project_id"),  # the owner's opt-ins are recorded here (authorization for runs)
             data={"auto_import": bool(data.get("auto_import")), "pdf_download": bool(data.get("pdf_download")),
                   "cadence": data["cadence"]})
    return get(conn, sid)


def get(conn: sqlite3.Connection, sid: str) -> Row:
    s = row(conn.execute("SELECT * FROM saved_searches WHERE id=?", (sid,)))
    if s is None:
        raise not_found("saved search")
    return {**s, "query": jload(s["query"]), "sources": jload(s["sources"]), "auto_import": bool(s["auto_import"]),
            "pdf_download": bool(s["pdf_download"]), "enabled": bool(s["enabled"])}


def set_enabled(conn: sqlite3.Connection, sid: str, enabled: bool, actor: str) -> Row:
    get(conn, sid)
    with tx(conn):
        conn.execute("UPDATE saved_searches SET enabled=?, updated_at=? WHERE id=?", (int(enabled), utcnow(), sid))
        emit(conn, "saved_search.enabled" if enabled else "saved_search.disabled", actor=actor,
             entity_type="saved_search", entity_id=sid, data={})
    return get(conn, sid)


def trigger(conn: sqlite3.Connection, kinds: KindRegistry, now: datetime) -> int:
    """Enqueue one run per due saved search (slot = its scheduled time) and the weekly retraction check."""
    n = 0
    due = rows(conn.execute("SELECT id, cadence, next_run_at, project_id FROM saved_searches WHERE enabled=1 AND "
                            "cadence!='manual' AND next_run_at IS NOT NULL AND next_run_at<=?", (_iso(now),)))
    for s in due:
        _, created = task_svc.create(conn, kinds, kind="literature.saved_search",
                                     payload={"saved_search_id": s["id"]}, actor="system:scheduler",
                                     idempotency_key=f"ss:{s['id']}:{s['next_run_at']}", project_id=s["project_id"])
        with tx(conn):  # next slot from NOW: a long sleep yields one catch-up run, not a backlog
            conn.execute("UPDATE saved_searches SET next_run_at=? WHERE id=?",
                         (_iso(now + CADENCE[s["cadence"]]), s["id"]))
        n += int(created)
    if oa.lit_settings(conn)["retraction_checks"]:
        week = now.strftime("%G-W%V")
        _, created = task_svc.create(conn, kinds, kind="literature.retraction_check", payload={},
                                     actor="system:scheduler", idempotency_key=f"retractions:{week}")
        n += int(created)
    return n


def _seen_before(conn: sqlite3.Connection, sid: str, run_id: str) -> set[str]:
    seen: set[str] = set()
    for r in rows(conn.execute("SELECT candidate_ids FROM lit_search_runs WHERE saved_search_id=? AND id!=?",
                               (sid, run_id))):
        seen |= set(jload(r["candidate_ids"]) or [])
    return seen


def run(conn: sqlite3.Connection, ctx: discovery.Ctx, writer: DbKnowledgeWriter, sid: str) -> dict[str, Any]:
    s = get(conn, sid)
    q = s["query"]
    filters = dict(q.get("filters") or {})
    if s["last_run_at"]:  # catch-up: only publications since the last run's year (dedupe handles the overlap)
        filters.setdefault("from_year", s["last_run_at"][:4])
    if s["kind"] == "author":
        filters["author"] = q["author"]
    res = discovery.search(conn, discovery.Ctx(ctx.ext, ctx.keystore, ctx.actor, s["project_id"]),
                           q.get("text") or q["author"], s["sources"], filters=filters, saved_search_id=sid,
                           kind="author" if s["kind"] == "author" else "search")
    new = [c for c in res["candidate_ids"] if c not in _seen_before(conn, sid, res["id"])]
    suggestions: dict[str, str] = {}
    if s["review_id"] and new:
        screening.add_items(conn, s["review_id"], new, res["id"])
        suggestions = screening.auto_screen(conn, s["review_id"], new)
    imported: list[dict[str, Any]] = []
    if s["auto_import"] and new:
        keep = [c for c in new if suggestions.get(c) != "exclude"]
        imported = importing.import_candidates(conn, ctx, writer, keep, authorized_by=f"saved_search:{sid}",
                                               with_pdf=s["pdf_download"])
    with tx(conn):
        conn.execute("UPDATE saved_searches SET last_run_at=?, updated_at=? WHERE id=?", (utcnow(), utcnow(), sid))
        errors = {k: v["error"] for k, v in res["per_source"].items() if "error" in v}
        if new or errors:
            conn.execute("INSERT INTO alerts(id,kind,subject_id,data,created_at) VALUES (?,?,?,?,?)",
                         (new_id("alert"), "new_papers" if new else "source_error", sid,
                          jdump({"saved_search": s["name"], "new": len(new), "candidate_ids": new[:100],
                                 "run_id": res["id"], "source_errors": errors,
                                 "auto_excluded": sum(1 for c in new if suggestions.get(c) == "exclude"),
                                 "imported": len(imported)}), utcnow()))
    return {"run_id": res["id"], "new": new, "suggestions": suggestions, "imported": imported,
            "per_source": res["per_source"]}
