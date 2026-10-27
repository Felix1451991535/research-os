"""Evidence weight labels (research-method feature 4) — labels, never a single score, never guessed:
- venue tier only from owner-imported tables (e.g. JCR quartile / CAS zone, with source + year), matched by ISSN or
  exact normalized venue name; no match -> "tier unknown";
- publication status from stored facts: `preprint` (kind preprint, or arXiv id without DOI/venue) vs `published`
  (peer review itself cannot be verified here and is not claimed);
- retraction from `works.retraction` (unknown stays unknown)."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...kernel.db import jdump, jload, row, rows, tx, utcnow
from ...kernel.events import emit

KEY = "venue_tiers"


def _norm(v: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]+", " ", v.casefold()).strip()


def set_tiers(
    conn: sqlite3.Connection, *, source: str, year: int, entries: list[dict[str, str]], actor: str
) -> dict[str, Any]:
    """Owner import (replaces the table for this source). entries: {venue or issn, tier}."""
    if not source.strip() or not entries or len(entries) > 50_000:
        raise ApiError(422, "bad_tiers", "source and 1..50000 entries required")
    table: dict[str, str] = {}
    for e in entries:
        tier = str(e.get("tier") or "").strip()
        key = str(e.get("issn") or "").strip().upper() or _norm(str(e.get("venue") or ""))
        if not tier or not key:
            raise ApiError(422, "bad_tiers", f"each entry needs a tier and a venue or ISSN: {e}")
        table[key] = tier
    cur = row(conn.execute("SELECT value_json FROM settings_kv WHERE key=?", (KEY,)))
    all_t = {**(jload(cur["value_json"]) if cur else {}), source: {"year": year, "table": table}}
    with tx(conn):
        conn.execute(
            "INSERT INTO settings_kv(key,value_json,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
            "SET value_json=excluded.value_json, updated_at=excluded.updated_at",
            (KEY, jdump(all_t), utcnow()),
        )
        emit(
            conn,
            "library.venue_tiers_imported",
            actor=actor,
            entity_type="settings",
            entity_id=KEY,
            data={"source": source, "year": year, "entries": len(table)},
        )
    return {"source": source, "year": year, "entries": len(table)}


def load_tiers(conn: sqlite3.Connection) -> dict[str, Any]:
    """The owner-imported tier tables (parse once per request; may hold 50k entries per source)."""
    cur = row(conn.execute("SELECT value_json FROM settings_kv WHERE key=?", (KEY,)))
    return dict(jload(cur["value_json"])) if cur else {}


def _tiers(tables: dict[str, Any], venue: str | None, issns: list[str]) -> list[dict[str, Any]]:
    out = []
    for source, t in tables.items():
        hit = next((t["table"][i] for i in issns if i in t["table"]), None)
        if hit is None and venue:
            hit = t["table"].get(_norm(venue))
        if hit is not None:
            out.append({"tier": hit, "source": source, "year": t["year"]})
    return out


def weight(conn: sqlite3.Connection, work_id: str, tables: dict[str, Any] | None = None) -> dict[str, Any]:
    """`tables` = load_tiers(conn), passed in by callers that label many works (library listing)."""
    w = row(conn.execute("SELECT kind, venue, retraction FROM works WHERE id=?", (work_id,)))
    if w is None:
        raise not_found("work")
    idents = rows(conn.execute("SELECT scheme, value FROM identifiers WHERE work_id=?", (work_id,)))
    ids = {r["scheme"]: r["value"] for r in idents}
    issns = [r["value"].upper() for r in idents if r["scheme"] == "issn"]
    preprint = w["kind"] == "preprint" or ("arxiv" in ids and "doi" not in ids and not w["venue"])
    retraction = jload(w["retraction"]) or {}
    tiers = _tiers(load_tiers(conn) if tables is None else tables, w["venue"], issns)
    labels = ["preprint (not peer-reviewed)" if preprint else "published"]
    labels += [f"{t['tier']} ({t['source']} {t['year']})" for t in tiers] or ["tier unknown"]
    if retraction.get("retracted"):
        labels.insert(0, "RETRACTED")
    elif retraction.get("retracted") is None:
        labels.append("retraction status unknown")
    return {
        "work_id": work_id,
        "publication": "preprint" if preprint else "published",
        "tiers": tiers,
        "retracted": retraction.get("retracted"),
        "labels": labels,
    }
