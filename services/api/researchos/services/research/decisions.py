"""Decision records (owner order c, ADR-184): why each research choice was made, linked to what it rests on.
A record is an `entities` row of type `decision` - written by the owner, never by a model - with `based_on`
relations to evidence, claims, answers, runs, plan revisions, studies or docs. Records are immutable; a changed
mind is a new record that `supersedes` the old one, so the history stays readable.

On read every link is re-resolved: missing targets, failed evidence/claims and failed runs are reported, and the
record's support is computed - supported (at least one live evidence, claim or succeeded-run link) | owner_judgement
(no such link: the choice rests on the stated rationale only) - never inferred by a model."""

from __future__ import annotations

import sqlite3
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...kernel.db import jdump, jload, new_id, row, rows, utcnow
from ...kernel.events import emit

KINDS = ("topic", "dataset", "method", "split", "metric", "hyperparameters", "stopping", "other")
LINKS = ("evidence", "claim", "answer", "run", "plan", "study", "doc")
SUPPORTING = ("evidence", "claim", "run")


def _resolve(conn: sqlite3.Connection, ref: str) -> dict[str, Any]:
    kind, _, ident = ref.partition(":")
    q = {
        "evidence": ("SELECT trust AS state, project_id FROM evidence WHERE id=?", (ident,)),
        "claim": ("SELECT trust AS state, project_id FROM entities WHERE type='claim' AND id=?", (ident,)),
        "answer": ("SELECT status AS state, project_id FROM answers WHERE id=?", (ident,)),
        "run": ("SELECT state FROM runs WHERE id=?", (ident,)),
        "study": ("SELECT stage AS state FROM studies WHERE id=?", (ident,)),
        "doc": ("SELECT kind AS state, project_id FROM docs WHERE id=?", (ident,)),
        "plan": ("SELECT 'revision' AS state FROM plan_revisions WHERE plan_id=? AND revision=?",
                 (ident.partition("@")[0], ident.partition("@")[2])),
    }.get(kind)
    if q is None or not ident:
        raise ApiError(422, "bad_link", f"link must be one of {', '.join(k + ':<id>' for k in LINKS)} "
                       "(plan:<id>@<revision>)", details={"link": ref})
    r = row(conn.execute(*q))
    state = None if r is None else str(r["state"])
    live = r is not None and state not in ("failed", "cancelled", "interrupted", "blocked")
    owner = r.get("project_id") if r is not None else None
    return {"ref": ref, "kind": kind, "exists": r is not None, "state": state, "live": live, "project_id": owner}


def create(conn: sqlite3.Connection, project_id: str, rec: dict[str, Any], actor: str,
           supersedes: str | None = None) -> dict[str, Any]:
    """Inside the caller's transaction (route idempotency)."""
    if rec["kind"] not in KINDS:
        raise ApiError(422, "bad_kind", f"kind must be one of {list(KINDS)}")
    links = list(dict.fromkeys(rec.get("links") or []))
    resolved = [_resolve(conn, ref) for ref in links]
    missing = [x["ref"] for x in resolved if not x["exists"]]
    if missing:
        raise ApiError(422, "unknown_link", "every link must name an existing record", details={"links": missing})
    foreign = [x["ref"] for x in resolved if x["project_id"] not in (None, project_id)]
    if foreign:  # records of another project are not evidence for this one (and would leak their existence)
        raise ApiError(422, "unknown_link", "every link must name an existing record", details={"links": foreign})
    if supersedes:
        old = get(conn, supersedes)
        if old["project_id"] != project_id:
            raise not_found("decision")
        if old["superseded_by"]:
            raise ApiError(409, "already_superseded", "supersede the newest record instead",
                           details={"superseded_by": old["superseded_by"]})
    did, now = new_id("dec"), utcnow()
    data = {k: rec.get(k) for k in ("kind", "question", "choice", "alternatives", "rationale")}
    conn.execute("INSERT INTO entities(id,type,project_id,data,trust,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                 (did, "decision", project_id, jdump(data), "human_checked", now, now))
    for ref in links:
        conn.execute("INSERT INTO relations(src,type,dst,attrs,created_at) VALUES (?,?,?,?,?)",
                     (did, "based_on", ref, "{}", now))
    if supersedes:
        conn.execute("INSERT INTO relations(src,type,dst,attrs,created_at) VALUES (?,?,?,?,?)",
                     (did, "supersedes", supersedes, "{}", now))
    emit(conn, "decision.recorded", actor=actor, entity_type="decision", entity_id=did, project_id=project_id,
         data={"kind": data["kind"], "links": len(links), "supersedes": supersedes})
    return get(conn, did)


def get(conn: sqlite3.Connection, decision_id: str) -> dict[str, Any]:
    e = row(conn.execute("SELECT * FROM entities WHERE type='decision' AND id=?", (decision_id,)))
    if e is None:
        raise not_found("decision")
    links = [_resolve(conn, r["dst"]) for r in rows(conn.execute(
        "SELECT dst FROM relations WHERE src=? AND type='based_on' ORDER BY rowid", (decision_id,)))]
    newer = row(conn.execute("SELECT src FROM relations WHERE type='supersedes' AND dst=?", (decision_id,)))
    older = row(conn.execute("SELECT dst FROM relations WHERE type='supersedes' AND src=?", (decision_id,)))
    supported = any(x["live"] and x["kind"] in SUPPORTING and (x["kind"] != "run" or x["state"] == "succeeded")
                    for x in links)
    return {"id": e["id"], "project_id": e["project_id"], **jload(e["data"]), "created_at": e["created_at"],
            "links": links, "broken_links": [x["ref"] for x in links if not x["live"]],
            "support": "supported" if supported else "owner_judgement",
            "supersedes": older["dst"] if older else None, "superseded_by": newer["src"] if newer else None}


def listing(conn: sqlite3.Connection, project_id: str, include_superseded: bool = False) -> list[dict[str, Any]]:
    out = [get(conn, r["id"]) for r in rows(conn.execute(
        "SELECT id FROM entities WHERE type='decision' AND project_id=? ORDER BY created_at, rowid", (project_id,)))]
    return out if include_superseded else [d for d in out if not d["superseded_by"]]
