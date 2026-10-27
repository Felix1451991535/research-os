"""Claim graph (K1 registered entity type `claim` + relations): claims come only from verified, page-cited answer
sentences and keep their Evidence ids (`supported_by`). Contradictions between claims of DIFFERENT works are model
suggestions (evidence-bearing prompt, certification required) stored as `contradicts` relations with status
suggested; only the owner confirms or rejects them."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...contracts.gateway import TaskType
from ...kernel.db import jdump, jload, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from ..gateway.attempt import GatewayError
from ..gateway.gateway import Gateway
from ..gateway.router import Signals
from .deep_prompts import CONTRADICTIONS

MAX_CLAIMS = 40


def from_answer(conn: sqlite3.Connection, answer: dict[str, Any], *, project_id: str, origin: dict[str, Any],
                actor: str) -> list[str]:
    ids = []
    with tx(conn):
        for s in answer.get("sentences") or []:
            ev = list(s.get("evidence_ids") or [])
            if not ev:
                continue
            trust = [r["trust"] for r in rows(conn.execute(
                f"SELECT trust FROM evidence WHERE id IN ({','.join('?' * len(ev))})", ev))]
            works = sorted({r["work_id"] for r in rows(conn.execute(
                "SELECT DISTINCT v.work_id FROM evidence e JOIN document_versions v ON v.version_id=e.version_id "
                f"WHERE e.id IN ({','.join('?' * len(ev))})", ev))})
            cid, now = new_id("claim"), utcnow()
            conn.execute("INSERT INTO entities(id,type,project_id,data,trust,created_at,updated_at) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (cid, "claim", project_id, jdump({"text": s["text"], "kind": s.get("kind"),
                                                           "evidence_ids": ev, "work_ids": works,
                                                           "answer_id": answer["id"], **origin}),
                          "failed" if "failed" in trust else "machine_checked", now, now))
            for e in ev:
                conn.execute("INSERT OR IGNORE INTO relations(src,type,dst,attrs,created_at) VALUES (?,?,?,?,?)",
                             (cid, "supported_by", f"evidence:{e}", "{}", now))
            ids.append(cid)
        if ids:
            emit(conn, "claims.created", actor=actor, entity_type="answer", entity_id=answer["id"],
                 project_id=project_id, data={"claims": len(ids)})
    return ids


def get_many(conn: sqlite3.Connection, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    out = {r["id"]: {**r, "data": jload(r["data"])} for r in rows(conn.execute(
        f"SELECT * FROM entities WHERE type='claim' AND id IN ({','.join('?' * len(ids))})", ids))}
    return [out[i] for i in ids if i in out]


def _quotes(conn: sqlite3.Connection, ev: list[str]) -> str:
    q = rows(conn.execute(f"SELECT quote FROM evidence WHERE id IN ({','.join('?' * len(ev))})", ev)) if ev else []
    return " | ".join(r["quote"][:300] for r in q)


def _check(text: str, keyed: dict[str, dict[str, Any]]) -> tuple[Signals, list[dict[str, Any]]]:
    try:
        pairs = json.loads(text).get("pairs")
    except (ValueError, AttributeError):
        return Signals(validators_passed=0.0), []
    if not isinstance(pairs, list):
        return Signals(validators_passed=0.0), []
    ok = [p for p in pairs if isinstance(p, dict) and p.get("a") in keyed and p.get("b") in keyed
          and p["a"] != p["b"] and str(p.get("aspect") or "").strip()
          and set(keyed[p["a"]]["data"]["work_ids"]).isdisjoint(keyed[p["b"]]["data"]["work_ids"])]
    return Signals(validators_passed=1.0 if len(ok) == len(pairs) else 0.5), ok


def flag_contradictions(conn: sqlite3.Connection, gw: Gateway, claim_ids: list[str], *, project_id: str,
                        actor: str) -> dict[str, Any]:
    cl = [c for c in get_many(conn, claim_ids) if c["trust"] != "failed"][:MAX_CLAIMS]
    if len({w for c in cl for w in c["data"]["work_ids"]}) < 2:
        return {"pairs": [], "note": "fewer than two sources: nothing to compare"}
    keyed = {f"K{i + 1}": c for i, c in enumerate(cl)}
    text = "\n".join(f"[{k}] {c['data']['text']}\n    quote: {_quotes(conn, c['data']['evidence_ids'])}"
                     for k, c in keyed.items())

    def validate(t: str) -> tuple[Signals, list[dict[str, Any]]]:
        return _check(t, keyed)

    try:
        g = gw.call(conn, task_type=TaskType.ROUTINE_TEXT, prompt=CONTRADICTIONS, variables={"claims": text},
                    validate=validate, project_id=project_id, evidence_bearing=True)
    except GatewayError as e:
        return {"pairs": [], "error": e.message}
    with tx(conn):
        for p in g.payload:
            a, b = keyed[p["a"]]["id"], keyed[p["b"]]["id"]
            conn.execute("INSERT OR IGNORE INTO relations(src,type,dst,attrs,created_at) VALUES (?,?,?,?,?)",
                         (a, "contradicts", b, jdump({"aspect": p["aspect"], "explanation": p.get("explanation"),
                                                      "status": "suggested", "by": g.alias,
                                                      "call_ids": g.call_ids}), utcnow()))
        emit(conn, "claims.contradictions_flagged", actor=actor, entity_type="project", entity_id=project_id,
             project_id=project_id, data={"pairs": len(g.payload)})
    return {"pairs": [{"a": keyed[p["a"]]["id"], "b": keyed[p["b"]]["id"], "aspect": p["aspect"]} for p in g.payload],
            "call_ids": g.call_ids}


def contradictions(conn: sqlite3.Connection, claim_ids: list[str]) -> list[dict[str, Any]]:
    if not claim_ids:
        return []
    marks = ",".join("?" * len(claim_ids))
    return [{**r, "attrs": jload(r["attrs"])} for r in rows(conn.execute(
        f"SELECT src, dst, attrs FROM relations WHERE type='contradicts' AND src IN ({marks})", claim_ids))]


def review_contradiction(conn: sqlite3.Connection, a: str, b: str, status: str, actor: str) -> dict[str, Any]:
    if status not in ("confirmed", "rejected"):
        raise ApiError(422, "bad_status", "status must be confirmed or rejected")
    r = row(conn.execute("SELECT attrs FROM relations WHERE src=? AND type='contradicts' AND dst=?", (a, b)))
    if r is None:
        raise not_found("contradiction")
    attrs = {**jload(r["attrs"]), "status": status, "reviewed_by": actor, "reviewed_at": utcnow()}
    with tx(conn):
        conn.execute("UPDATE relations SET attrs=? WHERE src=? AND type='contradicts' AND dst=?", (jdump(attrs), a, b))
        emit(conn, "claims.contradiction_reviewed", actor=actor, entity_type="claim", entity_id=a,
             data={"other": b, "status": status})
    return attrs
