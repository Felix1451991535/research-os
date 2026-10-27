"""Literature candidates: every search hit, deduped across sources by hard identifier (DOI > PMID > arXiv > Zotero),
never by title (fuzzy title matches are not merged). Every sighting keeps its source and run (provenance)."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, replace
from typing import Any

from ...contracts.datasources import BibRecord
from ...contracts.errors import not_found
from ...kernel.db import Row, jdump, jload, new_id, row, rows, utcnow
from ..knowledge.imports import record_from_dict
from ..knowledge.metadata import normalize_doi

HARD = ("doi", "pmid", "arxiv", "zotero")


def dedupe_key(r: BibRecord) -> str:
    ids: dict[str, str] = {s: v for s, v in r.identifiers}
    for scheme in HARD:
        if ids.get(scheme):
            v = normalize_doi(ids[scheme]) if scheme == "doi" else ids[scheme].strip()
            return f"{scheme}:{v}"
    return f"src:{r.source}:{r.source_id}"


def normalized(r: BibRecord) -> BibRecord:
    """Hard identifiers normalized (DOI lower-case, no URL prefix) whatever the plugin returned."""
    ids = tuple((s, normalize_doi(v) if s == "doi" else v.strip()) for s, v in r.identifiers)
    return replace(r, identifiers=ids) if ids != r.identifiers else r


def upsert(conn: sqlite3.Connection, r: BibRecord, run_id: str) -> tuple[str, bool]:
    """Caller holds the transaction. Returns (candidate id, created)."""
    r = normalized(r)
    key, now = dedupe_key(r), utcnow()
    sighting = {"source": r.source, "source_id": r.source_id, "run_id": run_id, "at": now}
    old = row(conn.execute("SELECT id, provenance FROM lit_candidates WHERE dedupe_key=?", (key,)))
    if old:
        prov = jload(old["provenance"]) or []
        if not any(p["source"] == r.source and p["source_id"] == r.source_id and p["run_id"] == run_id for p in prov):
            prov.append(sighting)
        conn.execute("UPDATE lit_candidates SET provenance=?, updated_at=? WHERE id=?", (jdump(prov), now, old["id"]))
        return str(old["id"]), False
    cid = new_id("cand")
    conn.execute("INSERT INTO lit_candidates(id,dedupe_key,record,provenance,created_at,updated_at) "
                 "VALUES (?,?,?,?,?,?)", (cid, key, jdump(asdict(r)), jdump([sighting]), now, now))
    return cid, True


def get(conn: sqlite3.Connection, cid: str) -> Row:
    c = row(conn.execute("SELECT * FROM lit_candidates WHERE id=?", (cid,)))
    if c is None:
        raise not_found("candidate")
    return view(c)


def view(c: Row) -> dict[str, Any]:
    return {**c, "record": jload(c["record"]), "provenance": jload(c["provenance"]),
            "retracted": None if c["retracted"] is None else bool(c["retracted"])}


def record_of(c: Row) -> BibRecord:
    rec = c["record"] if isinstance(c["record"], dict) else jload(c["record"])
    return record_from_dict(rec)


def many(conn: sqlite3.Connection, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    found = {c["id"]: view(c) for c in rows(conn.execute(
        f"SELECT * FROM lit_candidates WHERE id IN ({','.join('?' * len(ids))})", ids))}
    return [found[i] for i in ids if i in found]
