"""Deep research pipeline (K2 task `research.deep_advance`; ARCHITECTURE §4 think/deep research).

planning -> awaiting_plan_approval (K3 research_checkpoint on question+plan+settings) -> searching (round 1: every
sub-question query on every source) -> awaiting_screening (rule + optional model SUGGESTIONS; the owner decides;
round 2 = citation chaining from included records, screened again) -> awaiting_import_approval (K3 knowledge_import
of exactly the included records) -> reading (wait for PDF parsing) -> synthesizing (page-cited, independently
verified answer per sub-question -> claims -> contradiction suggestions -> report) -> completed.
Models never decide inclusion; nothing is synthesized from abstracts or memory."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...contracts.errors import ApiError
from ...contracts.gateway import TaskType
from ...kernel.db import jdump, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from ..gateway.attempt import GatewayError
from ..gateway.router import Signals
from ..knowledge import imports
from ..knowledge.writer import DbKnowledgeWriter
from ..literature import candidates, discovery, importing, oa, screening, screening_model
from . import claims, deep_report, qa
from . import deep_store as store
from .deep_prompts import DR_PLAN

DONE_PARSE = ("parsed", "partial", "failed", "unsupported", "needs_ocr")


def create(conn: sqlite3.Connection, svc: Any, *, project_id: str, question: str, sources: list[str], actor: str,
           sub_questions: list[dict[str, Any]] | None = None, rounds: int = 2, page_size: int = 10,
           use_model_screening: bool = False, criteria: dict[str, Any] | None = None) -> dict[str, Any]:
    unknown = [s for s in sources if s not in svc.extensions.sources]
    if not sources or unknown:
        raise ApiError(422, "unknown_source", f"unknown or no sources {unknown}")
    plan = {"sub_questions": sub_questions} if sub_questions else None
    if plan:
        _validate_plan(plan)
    rev = screening.create(conn, title=f"Deep research: {question[:80]}", question=question, actor=actor,
                           project_id=project_id, criteria=criteria or {"include": [question], "exclude": []})
    rid, now = new_id("dr"), utcnow()
    settings = {"sources": sources, "rounds": max(1, min(2, rounds)), "page_size": page_size,
                "use_model_screening": use_model_screening}
    with tx(conn):
        conn.execute("INSERT INTO deep_research(id,project_id,question,settings,plan,review_id,stage,data,created_by,"
                     "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, project_id, question, jdump(settings), jdump(plan) if plan else None, rev["id"],
                      "awaiting_plan_approval" if plan else "planning", "{}", actor, now, now))
        emit(conn, "deep_research.created", actor=actor, entity_type="deep_research", entity_id=rid,
             project_id=project_id, data={"sources": sources})
    return store.get(conn, rid)


def _validate_plan(plan: Any) -> list[dict[str, Any]]:
    sq = plan.get("sub_questions") if isinstance(plan, dict) else None
    ok = isinstance(sq, list) and 1 <= len(sq) <= 6 and all(
        isinstance(s, dict) and isinstance(s.get("q"), str) and 3 <= len(s["q"]) <= 300
        and isinstance(s.get("queries"), list) and 1 <= len(s["queries"]) <= 3
        and all(isinstance(q, str) and 2 <= len(q) <= 200 for q in s["queries"]) for s in sq)
    if not ok:
        raise ApiError(422, "bad_plan", "plan needs 1-6 sub-questions, each with 1-3 queries")
    return list(sq)  # type: ignore[arg-type]


def _planning(conn: sqlite3.Connection, svc: Any, r: dict[str, Any], actor: str) -> str:
    def validate(t: str) -> tuple[Signals, Any]:
        try:
            plan = json.loads(t)
            _validate_plan(plan)
            return Signals(), plan
        except (ValueError, ApiError):
            return Signals(validators_passed=0.0), None

    try:
        g = svc.gateway.call(conn, task_type=TaskType.ROUTINE_TEXT, prompt=DR_PLAN,
                             variables={"question": r["question"]}, validate=validate, project_id=r["project_id"])
    except GatewayError as e:
        store.save(conn, r["id"], actor, data={**r["data"], "planning_error": e.message})
        return "planning"  # owner can supply sub-questions instead (create with sub_questions)
    store.save(conn, r["id"], actor, plan=g.payload, stage="awaiting_plan_approval",
               data={**r["data"], "plan_calls": g.call_ids})
    return "awaiting_plan_approval"


def _approved_plan(conn: sqlite3.Connection, r: dict[str, Any]) -> bool:
    a = row(conn.execute("SELECT subject_hash FROM approvals WHERE subject_type='research_checkpoint' AND "
                         "subject_id=? AND revoked_at IS NULL AND expires_at>? ORDER BY created_at DESC LIMIT 1",
                         (f"{r['id']}:plan", utcnow())))
    return a is not None and a["subject_hash"] == store.plan_hash(r)


def _ctx(svc: Any, r: dict[str, Any], actor: str) -> discovery.Ctx:
    return discovery.Ctx(svc.extensions, svc.keystore, actor, r["project_id"])


def _suggest(conn: sqlite3.Connection, svc: Any, r: dict[str, Any], ids: list[str], actor: str) -> dict[str, Any]:
    out: dict[str, Any] = {"rules": screening.auto_screen(conn, r["review_id"], ids, actor)}
    if r["settings"]["use_model_screening"] and ids:
        out["model"] = screening_model.suggest(conn, svc.gateway, r["review_id"], ids, actor)
    return out


def _searching(conn: sqlite3.Connection, svc: Any, r: dict[str, Any], actor: str) -> str:
    runs, ids = [], []
    for sq in r["plan"]["sub_questions"]:
        for q in sq["queries"]:
            res = discovery.search(conn, _ctx(svc, r, actor), q, r["settings"]["sources"],
                                   page_size=r["settings"]["page_size"])
            runs.append(res["id"])
            screening.add_items(conn, r["review_id"], res["candidate_ids"], res["id"])
            ids += [c for c in res["candidate_ids"] if c not in ids]
    sug = _suggest(conn, svc, r, ids, actor)
    store.save(conn, r["id"], actor, stage="awaiting_screening",
               data={**r["data"], "round": 1, "runs": runs, "suggestions_round1": _brief(sug)})
    return "awaiting_screening"


def _brief(sug: dict[str, Any]) -> dict[str, Any]:
    m = sug.get("model") or {}
    return {"rules": len(sug["rules"]), "model": len(m.get("suggested") or {}), "model_error": m.get("error")}


def _decided(conn: sqlite3.Connection, r: dict[str, Any]) -> tuple[bool, list[str]]:
    items = screening.items(conn, r["review_id"])
    pending = [i for i in items if i["screening"]["title_abstract"]["decision"] is None]
    included = [i["candidate"]["id"] for i in items if i["screening"]["title_abstract"]["decision"] == "include"]
    return not pending, included


def _chain_round(conn: sqlite3.Connection, svc: Any, r: dict[str, Any], included: list[str], actor: str) -> list[str]:
    new: list[str] = []
    capable = [n for n in r["settings"]["sources"] if hasattr(svc.extensions.sources[n], "cited_by")]
    if not capable:
        return new
    for cid in included[:10]:
        rec = candidates.record_of(candidates.get(conn, cid))
        for direction in ("references", "cited_by"):
            res = discovery.chain(conn, _ctx(svc, r, actor), rec, direction, capable, r["settings"]["page_size"])
            screening.add_items(conn, r["review_id"], res["candidate_ids"], res["id"])
            new += [c for c in res["new_candidate_ids"] if c not in new]
    return new


def _screening(conn: sqlite3.Connection, svc: Any, r: dict[str, Any], actor: str) -> str:
    done, included = _decided(conn, r)
    if not done:
        return "awaiting_screening"
    if r["data"].get("round", 1) < r["settings"]["rounds"]:
        new = _chain_round(conn, svc, r, included, actor)
        sug = _suggest(conn, svc, r, new, actor)
        r = store.save(conn, r["id"], actor, data={**r["data"], "round": 2, "round2_new": len(new),
                                                    "suggestions_round2": _brief(sug)})
        if new:
            return "awaiting_screening"
        _, included = _decided(conn, r)
    if not included:
        store.save(conn, r["id"], actor, stage="stopped", data={**r["data"], "stop_reason": "nothing included"})
        return "stopped"
    recs = [candidates.record_of(c) for c in candidates.many(conn, included)]
    store.save(conn, r["id"], actor, stage="awaiting_import_approval",
               data={**r["data"], "selection": included, "selection_hash": imports.records_hash(recs)})
    return "awaiting_import_approval"


def _importing(conn: sqlite3.Connection, svc: Any, r: dict[str, Any], actor: str) -> str:
    sel = r["data"]["selection"]
    try:
        h = imports.consume_approval(conn, [candidates.record_of(c) for c in candidates.many(conn, sel)], actor)
    except ApiError:
        return "awaiting_import_approval"
    writer = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.deep_research", actor=actor,
                               project_id=r["project_id"], max_bytes=svc.settings.max_upload_mb * 1024 * 1024)
    res = importing.import_candidates(conn, _ctx(svc, r, actor), writer, sel, authorized_by=f"approval:{h}",
                                      with_pdf=oa.lit_settings(conn)["pdf_download"])
    works = sorted({x["work_id"] for x in res if x.get("work_id")})
    store.save(conn, r["id"], actor, stage="reading", data={**r["data"], "imported": res, "works": works})
    return "reading"


def _reading(conn: sqlite3.Connection, svc: Any, r: dict[str, Any], actor: str) -> str:
    works = r["data"]["works"]
    if not works:
        return _go_synth(conn, r, actor)
    marks = ",".join("?" * len(works))
    vs = rows(conn.execute(f"SELECT work_id, parse_state FROM document_versions WHERE work_id IN ({marks})", works))
    if any(v["parse_state"] not in DONE_PARSE for v in vs):
        return "reading"
    return _go_synth(conn, r, actor)


def _go_synth(conn: sqlite3.Connection, r: dict[str, Any], actor: str) -> str:
    store.save(conn, r["id"], actor, stage="synthesizing")
    return "synthesizing"


def _synthesizing(conn: sqlite3.Connection, svc: Any, r: dict[str, Any], actor: str) -> str:
    answers, claim_ids = [], []
    for i, sq in enumerate(r["plan"]["sub_questions"]):
        a = qa.answer(conn, svc.gateway, qa.QAContext(sq["q"], r["project_id"], actor, None))
        answers.append({"sub_question": sq["q"], "answer_id": a["id"], "status": a["status"]})
        claim_ids += claims.from_answer(conn, a, project_id=r["project_id"], actor=actor,
                                        origin={"research_id": r["id"], "sub_question": i})
    flags = claims.flag_contradictions(conn, svc.gateway, claim_ids, project_id=r["project_id"], actor=actor)
    data = {**r["data"], "answers": answers, "claims": claim_ids, "contradictions": flags}
    r = store.save(conn, r["id"], actor, data=data)
    doc = deep_report.write(conn, svc, r, actor)
    store.save(conn, r["id"], actor, stage="completed", data={**data, "report_doc": doc["id"]})
    return "completed"


STAGES = {"planning": _planning, "searching": _searching, "awaiting_screening": _screening,
          "awaiting_import_approval": _importing, "reading": _reading, "synthesizing": _synthesizing}


def advance(conn: sqlite3.Connection, svc: Any, rid: str, actor: str) -> str:
    r = store.get(conn, rid)
    if r["stage"] == "awaiting_plan_approval":
        if not _approved_plan(conn, r):
            return "awaiting_plan_approval"
        r = store.save(conn, rid, actor, stage="searching")
    fn = STAGES.get(r["stage"])
    return fn(conn, svc, r, actor) if fn else str(r["stage"])


def accept_suggestions(conn: sqlite3.Connection, rid: str, actor: str) -> int:
    """Owner action: turn the latest title/abstract suggestions of undecided items into the owner's decisions
    (logged as decisions by the owner with the suggestion's reason). Items without a suggestion stay undecided."""
    r = store.get(conn, rid)
    n = 0
    for it in screening.items(conn, r["review_id"]):
        st = it["screening"]["title_abstract"]
        if st["decision"] is None and st["suggestion"] in ("include", "exclude"):
            reason = row(conn.execute(
                "SELECT reason FROM screening_decisions WHERE review_id=? AND candidate_id=? AND kind='suggestion' "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1", (r["review_id"], it["candidate"]["id"])))
            screening.decide(conn, r["review_id"], it["candidate"]["id"], "title_abstract", st["suggestion"],
                             f"accepted suggestion: {(reason or {}).get('reason', 'n/a')}", actor)
            n += 1
    return n
