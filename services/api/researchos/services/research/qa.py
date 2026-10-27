"""Cited question answering (baseline §5). No evidence -> "insufficient material" without any model call.
No configured model -> "not_configured" plus the retrieved passages (search still works without keys)."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ...contracts.gateway import TaskType
from ...contracts.knowledge import SearchHit
from ...contracts.trust import TrustStatus, composition
from ...kernel.db import jdump, new_id, tx, utcnow
from ...kernel.events import emit
from ..gateway import certification, egress
from ..gateway.attempt import GatewayError
from ..gateway.gateway import AINotConfigured, Gateway, GatewayResult
from ..gateway.router import Signals
from ..knowledge import evidence as ev
from ..knowledge.retrieval import FtsRetriever
from .qa_prompts import CITED_QA, VERIFY_SUPPORT
from .qa_validate import Checked, check_answer

MAX_PASSAGES = 8
PASSAGE_CHARS = 900


@dataclass
class QAContext:
    question: str
    project_id: str | None
    actor: str
    trace_id: str | None
    force_top: bool = False
    eval_mode: bool = False  # certification eval: gate bypassed, answer labelled "eval"
    work_id: str | None = None  # restrict evidence to one work (per-paper note card)


def _passages(conn: sqlite3.Connection, hits: list[SearchHit]) -> dict[str, SearchHit]:
    out: dict[str, SearchHit] = {}
    for h in hits:
        if ev.verify_chunk(conn, h.chunk_id):  # verify-before-use: never send a stale passage to a model
            out[f"E{len(out) + 1}"] = h
    return out


def _store(conn: sqlite3.Connection, ctx: QAContext, status: str, body: dict[str, Any], trust: dict[str, int],
           call_ids: list[str]) -> dict[str, Any]:
    aid = new_id("ans")
    body = {"egress_restricted": [], **body}  # every answer records its provenance; [] = no restricted data (#55)
    with tx(conn):
        conn.execute("INSERT INTO answers(id,project_id,question,status,body,trust_composition,call_ids,trace_id,"
                     "created_at) VALUES (?,?,?,?,?,?,?,?,?)", (aid, ctx.project_id, ctx.question, status, jdump(body),
                                                                jdump(trust), jdump(call_ids), ctx.trace_id, utcnow()))
        emit(conn, "answer.created", actor=ctx.actor, entity_type="answer", entity_id=aid, project_id=ctx.project_id,
             correlation_id=ctx.trace_id, data={"status": status, "calls": len(call_ids)})
    return {"id": aid, "status": status, "question": ctx.question, **body, "trust": trust, "call_ids": call_ids}


def _hit_json(label: str, h: SearchHit) -> dict[str, Any]:
    return {"label": label, "chunk_id": h.chunk_id, "version_id": h.version_id, "title": h.title,
            "page_index": h.page_index, "text": h.text[:PASSAGE_CHARS], "quality": h.quality}


def _bind_evidence(conn: sqlite3.Connection, ctx: QAContext, kept: list[dict[str, Any]],
                   psg: dict[str, SearchHit]) -> list[TrustStatus]:
    """Every kept sentence is bound to Evidence rows created through the single validated write path."""
    trusts: list[TrustStatus] = []
    for s in kept:
        ids = []
        for label in s.get("evidence", []):
            h = psg[label]
            e = ev.create(conn, version_id=h.version_id, page_index=h.page_index, quote=h.text[:PASSAGE_CHARS],
                          created_by=ctx.actor, project_id=ctx.project_id)
            ids.append(e["id"])
            trusts.append(TrustStatus(e["trust"]))
        s["evidence_ids"] = ids
    return trusts


def _verify_support(gw: Gateway, conn: sqlite3.Connection, ctx: QAContext, writer: GatewayResult, checked: Checked,
                    psg: dict[str, SearchHit], call_ids: list[str]) -> str:
    """Independent support check by a model from a DIFFERENT provider; only specific claims, never regenerates."""
    claims = [{"id": f"S{i + 1}", "claim": s["text"], "passages": [psg[x].text[:PASSAGE_CHARS] for x in s["evidence"]]}
              for i, s in enumerate(checked.kept) if s.get("evidence")]
    if not claims:
        return "not_needed"

    def validate(text: str) -> tuple[Signals, Any]:
        try:
            res = {r["id"]: bool(r["supported"]) for r in json.loads(text)["results"]}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise ValueError(str(e)) from e
        return Signals(validators_passed=1.0 if set(res) == {c["id"] for c in claims} else 0.0), res

    try:
        g = gw.call(conn, task_type=TaskType.ROUTINE_TEXT, prompt=VERIFY_SUPPORT,
                    variables={"claims": json.dumps(claims, ensure_ascii=False)}, validate=validate,
                    project_id=ctx.project_id, trace_id=ctx.trace_id, exclude_provider=writer.provider,
                    data_refs=egress.refs_of(writer.restricted))  # the verifier sees the writer's claims (#47)
    except (AINotConfigured, GatewayError):
        return "unverified"
    call_ids += g.call_ids
    keep = []
    for i, s in enumerate(checked.kept):
        if s.get("evidence") and not g.payload.get(f"S{i + 1}", False):
            checked.dropped.append({**s, "reason": "independent verifier: not supported by cited passages"})
        else:
            keep.append(s)
    checked.kept = keep
    return "verified"


def answer(conn: sqlite3.Connection, gw: Gateway, ctx: QAContext) -> dict[str, Any]:
    hits = FtsRetriever(conn).search(ctx.question, project_id=ctx.project_id, limit=MAX_PASSAGES, work_id=ctx.work_id)
    psg = _passages(conn, hits)
    shown = [_hit_json(k, h) for k, h in psg.items()]
    if not psg:
        return _store(conn, ctx, "insufficient_material", {"sentences": [], "dropped": [], "passages": []}, {}, [])
    text = "\n\n".join(f"[{k}] ({h.title}, PDF p.{h.page_index + 1})\n{h.text[:PASSAGE_CHARS]}" for k, h in psg.items())
    passages = {k: h.text[:PASSAGE_CHARS] for k, h in psg.items()}
    holder: dict[str, Checked] = {}

    def validate(t: str) -> tuple[Signals, Any]:
        sig, checked = check_answer(t, passages)
        holder["c"] = checked
        return sig, checked

    try:
        g = gw.call(conn, task_type=TaskType.ROUTINE_TEXT, prompt=CITED_QA, variables={"question": ctx.question,
                    "passages": text}, validate=validate, project_id=ctx.project_id, trace_id=ctx.trace_id,
                    force_top=ctx.force_top, evidence_bearing=not ctx.eval_mode)
    except AINotConfigured as e:
        return _store(conn, ctx, "not_configured", {"sentences": [], "dropped": [], "passages": shown,
                                                    "reason": e.message}, {}, [])
    checked: Checked = g.payload
    call_ids = list(g.call_ids)
    support = _verify_support(gw, conn, ctx, g, checked, psg, call_ids)
    trusts = _bind_evidence(conn, ctx, checked.kept, psg)
    status = "answered" if checked.kept else "insufficient_material"
    body = {"sentences": checked.kept, "dropped": checked.dropped, "passages": shown, "model": g.model_id,
            "labels": g.labels + ([] if support == "verified" else [f"support_{support}"]) +
            (["eval"] if ctx.eval_mode else []) +
            (["certification_override"] if certification.status(conn, g.model_id, CITED_QA.hash) == "override" else []),
            "escalated": g.escalated, "egress_restricted": list(g.restricted)}  # ("answer", id) refs inherit it
    return _store(conn, ctx, status, body, composition(trusts), call_ids)
