"""Systematic-review screening (PRISMA-style). Reviews have fixed criteria (hashed); the screening log is
append-only. Only a user action is a *decision*; rules and models write *suggestions* (kind='suggestion') that never
count in PRISMA numbers until a user confirms them. Latest decision per (candidate, stage) wins."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

import jsonschema

from ...contracts.errors import ApiError, not_found
from ...kernel.db import Row, jdump, jload, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from . import candidates

RULE = {"type": "object", "required": ["id", "field", "op", "value", "decision", "reason"],
        "properties": {"id": {"type": "string", "pattern": "^[a-z0-9_]{1,40}$"},
                       "field": {"enum": ["title", "abstract", "text", "year", "kind", "venue", "retracted"]},
                       "op": {"enum": ["contains", "not_contains", "gte", "lte", "equals"]},
                       "value": {}, "decision": {"enum": ["include", "exclude", "maybe"]},
                       "reason": {"type": "string", "minLength": 3}}, "additionalProperties": False}
CRITERIA = {"type": "object", "required": ["include", "exclude"],
            "properties": {"include": {"type": "array", "items": {"type": "string"}},
                           "exclude": {"type": "array", "items": {"type": "string"}},
                           "rules": {"type": "array", "items": RULE, "maxItems": 50}}, "additionalProperties": False}
STAGES = ("title_abstract", "full_text")
DEFAULT_RULES = [{"id": "retracted", "field": "retracted", "op": "equals", "value": True, "decision": "exclude",
                  "reason": "retracted publication"}]


def create(conn: sqlite3.Connection, *, title: str, question: str, criteria: dict[str, Any], actor: str,
           project_id: str | None = None) -> Row:
    try:
        jsonschema.validate(criteria, CRITERIA)
    except jsonschema.ValidationError as e:
        raise ApiError(422, "invalid_criteria", e.message) from None
    h = hashlib.sha256(jdump(criteria).encode()).hexdigest()
    rid = new_id("rev")
    with tx(conn):
        conn.execute("INSERT INTO lit_reviews(id,project_id,title,question,criteria,criteria_hash,created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (rid, project_id, title, question, jdump(criteria), h, utcnow()))
        emit(conn, "review.created", actor=actor, entity_type="lit_review", entity_id=rid, project_id=project_id,
             data={"criteria_hash": h})
    return get(conn, rid)


def get(conn: sqlite3.Connection, rid: str) -> Row:
    r = row(conn.execute("SELECT * FROM lit_reviews WHERE id=?", (rid,)))
    if r is None:
        raise not_found("review")
    return {**r, "criteria": jload(r["criteria"])}


def add_items(conn: sqlite3.Connection, rid: str, cand_ids: list[str], run_id: str | None) -> int:
    get(conn, rid)
    with tx(conn):
        n = sum(conn.execute("INSERT OR IGNORE INTO lit_review_items(review_id,candidate_id,search_run_id,added_at) "
                             "VALUES (?,?,?,?)", (rid, c, run_id, utcnow())).rowcount for c in cand_ids)
    return n


def _log(conn: sqlite3.Connection, rid: str, cid: str, stage: str, decision: str, reason: str, kind: str, actor: str,
         basis: dict[str, Any]) -> str:
    did = new_id("scr")
    conn.execute("INSERT INTO screening_decisions(id,review_id,candidate_id,stage,decision,reason,kind,actor,basis,"
                 "created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (did, rid, cid, stage, decision, reason, kind, actor, jdump(basis), utcnow()))
    return did


def decide(conn: sqlite3.Connection, rid: str, cid: str, stage: str, decision: str, reason: str, actor: str) -> str:
    """A user decision. Full-text screening requires a title/abstract include first (PRISMA flow)."""
    rev = get(conn, rid)
    if stage not in STAGES or decision not in ("include", "exclude", "maybe") or len(reason.strip()) < 3:
        raise ApiError(422, "bad_decision", "stage, decision and a reason (>= 3 chars) are required")
    if not row(conn.execute("SELECT 1 FROM lit_review_items WHERE review_id=? AND candidate_id=?", (rid, cid))):
        raise ApiError(404, "not_in_review", "candidate is not part of this review")
    if stage == "full_text" and latest(conn, rid, cid, "title_abstract") != "include":
        raise ApiError(409, "not_eligible", "include at title/abstract stage before full-text screening")
    with tx(conn):
        did = _log(conn, rid, cid, stage, decision, reason.strip(), "decision", actor,
                   {"criteria_hash": rev["criteria_hash"]})
        emit(conn, "screening.decided", actor=actor, entity_type="lit_review", entity_id=rid,
             data={"candidate_id": cid, "stage": stage, "decision": decision})
    return did


def latest(conn: sqlite3.Connection, rid: str, cid: str, stage: str, kind: str = "decision") -> str | None:
    r = row(conn.execute("SELECT decision FROM screening_decisions WHERE review_id=? AND candidate_id=? AND stage=? "
                         "AND kind=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (rid, cid, stage, kind)))
    return str(r["decision"]) if r else None


def _field(c: dict[str, Any], f: str) -> Any:
    rec = c["record"]
    if f == "retracted":
        return c["retracted"]
    if f == "text":
        return f"{rec.get('title') or ''} {rec.get('abstract') or ''}"
    return rec.get(f)


def _match(v: Any, op: str, want: Any) -> bool:
    if op in ("contains", "not_contains"):
        hit = isinstance(v, str) and str(want).casefold() in v.casefold()
        return hit if op == "contains" else not hit
    if op == "equals":
        return bool(v == want)
    if v is None:
        return False  # unknown never satisfies a numeric rule
    return bool(v >= want) if op == "gte" else bool(v <= want)


def auto_screen(conn: sqlite3.Connection, rid: str, cand_ids: list[str], actor: str = "system") -> dict[str, str]:
    """Deterministic rule suggestions at title/abstract stage (retracted -> exclude is always on). First matching
    exclude wins, then include, then maybe; no match -> no suggestion. Returns {candidate_id: suggestion}."""
    rev = get(conn, rid)
    rules = DEFAULT_RULES + list(rev["criteria"].get("rules") or [])
    out: dict[str, str] = {}
    with tx(conn):
        for c in candidates.many(conn, cand_ids):
            hits = [r for r in rules if _match(_field(c, r["field"]), r["op"], r["value"])]
            for want in ("exclude", "include", "maybe"):
                r = next((h for h in hits if h["decision"] == want), None)
                if r:
                    _log(conn, rid, c["id"], "title_abstract", want, r["reason"], "suggestion", f"rule:{r['id']}",
                         {"rule": r, "criteria_hash": rev["criteria_hash"]})
                    out[c["id"]] = want
                    break
    return out


def items(conn: sqlite3.Connection, rid: str) -> list[dict[str, Any]]:
    ids = [r["candidate_id"] for r in rows(conn.execute(
        "SELECT candidate_id FROM lit_review_items WHERE review_id=? ORDER BY added_at", (rid,)))]
    out = []
    for c in candidates.many(conn, ids):
        st = {s: {"decision": latest(conn, rid, c["id"], s), "suggestion": latest(conn, rid, c["id"], s, "suggestion")}
              for s in STAGES}
        out.append({"candidate": c, "screening": st})
    return out


def _reasons(conn: sqlite3.Connection, rid: str, stage: str, ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for cid in ids:
        r = row(conn.execute("SELECT reason FROM screening_decisions WHERE review_id=? AND candidate_id=? AND stage=? "
                             "AND kind='decision' ORDER BY created_at DESC, rowid DESC LIMIT 1", (rid, cid, stage)))
        if r:
            counts[r["reason"]] = counts.get(r["reason"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def prisma(conn: sqlite3.Connection, rid: str) -> dict[str, Any]:
    """PRISMA 2020 flow numbers computed from the logs (never estimated)."""
    rev = get(conn, rid)
    its = items(conn, rid)
    ids = [i["candidate"]["id"] for i in its]
    runs = {r["search_run_id"] for r in rows(conn.execute(
        "SELECT DISTINCT search_run_id FROM lit_review_items WHERE review_id=? AND search_run_id IS NOT NULL", (rid,)))}
    by_source: dict[str, int] = {}
    for i in its:
        for p in i["candidate"]["provenance"]:
            if p["run_id"] in runs:
                by_source[p["source"]] = by_source.get(p["source"], 0) + 1
    identified = sum(by_source.values())

    def dec(stage: str, d: str) -> list[str]:
        return [i["candidate"]["id"] for i in its if i["screening"][stage]["decision"] == d]

    ta_ex, ta_in = dec("title_abstract", "exclude"), dec("title_abstract", "include")
    ft_ex, ft_in = dec("full_text", "exclude"), dec("full_text", "include")
    return {"review_id": rid, "criteria_hash": rev["criteria_hash"], "identified_by_source": by_source,
            "records_identified": identified, "duplicates_removed": max(0, identified - len(ids)),
            "records_screened": len([i for i in its if i["screening"]["title_abstract"]["decision"]]),
            "records_excluded": len(ta_ex), "excluded_reasons": _reasons(conn, rid, "title_abstract", ta_ex),
            "awaiting_screening": len(ids) - len([i for i in its if i["screening"]["title_abstract"]["decision"]]),
            "full_text_assessed": len(ft_ex) + len(ft_in), "full_text_excluded": len(ft_ex),
            "full_text_excluded_reasons": _reasons(conn, rid, "full_text", ft_ex), "included": len(ft_in),
            "awaiting_full_text": len([c for c in ta_in if c not in ft_ex + ft_in]),
            "note": "rule/model suggestions are not counted until a user decides"}
