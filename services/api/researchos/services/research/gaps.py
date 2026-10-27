"""Evidence-gap map (owner order b, ADR-182): what the project's literature has and lacks, computed from the claim
graph only - verified, page-cited claims (claims.py), their works, evidence-weight labels and contradiction
relations. No model call. The owner names the axes (e.g. paradigm x method); a term matches a claim when it occurs
as whole words in the claim text or in one of its evidence quotes (case-insensitive; alternatives allowed).

Cell status, first match wins: contested (a confirmed or suggested contradiction between two of its claims) | gap
(no work) | thin (one work) | weak (every work a preprint or retracted) | covered (two or more works)."""

from __future__ import annotations

import re
import sqlite3
from itertools import product
from typing import Any

from ...contracts.errors import ApiError
from ...kernel.db import rows
from ..literature import weight
from . import claims

MAX_TERMS = 12


def _term(t: str | list[str]) -> tuple[str, re.Pattern[str]]:
    alts = [t] if isinstance(t, str) else list(t)
    alts = [a.strip() for a in alts if a.strip()]
    if not alts:
        raise ApiError(422, "bad_term", "every axis term needs at least one non-empty alternative")
    return alts[0], re.compile("|".join(rf"(?<!\w){re.escape(a)}(?!\w)" for a in alts), re.IGNORECASE)


def _texts(conn: sqlite3.Connection, c: dict[str, Any]) -> str:
    ev = c["data"]["evidence_ids"]
    quotes = rows(conn.execute(f"SELECT quote FROM evidence WHERE id IN ({','.join('?' * len(ev))})", ev))
    return "\n".join([c["data"]["text"], *(q["quote"] for q in quotes)])


def _status(cell: list[dict[str, Any]], contested: bool, labels: dict[str, dict[str, Any]]) -> str:
    works = {w for c in cell for w in c["data"]["work_ids"]}
    if contested:
        return "contested"
    if not works:
        return "gap"
    if len(works) == 1:
        return "thin"
    if all(labels[w]["publication"] == "preprint" or labels[w]["retracted"] for w in works):
        return "weak"
    return "covered"


def gap_map(conn: sqlite3.Connection, project_id: str, axes: dict[str, list[str | list[str]]]) -> dict[str, Any]:
    if not 1 <= len(axes) <= 2 or any(not 1 <= len(v) <= MAX_TERMS for v in axes.values()):
        raise ApiError(422, "bad_axes", f"give one or two axes with 1-{MAX_TERMS} terms each")
    names = list(axes)
    terms = {a: [_term(t) for t in axes[a]] for a in names}
    ids = [r["id"] for r in rows(conn.execute(
        "SELECT id FROM entities WHERE type='claim' AND project_id=? ORDER BY created_at", (project_id,)))]
    usable = [c for c in claims.get_many(conn, ids) if c["trust"] != "failed"]
    text = {c["id"]: _texts(conn, c) for c in usable}
    tables = weight.load_tiers(conn)
    labels = {w: weight.weight(conn, w, tables) for c in usable for w in c["data"]["work_ids"]}
    pairs = [(r["src"], r["dst"]) for r in claims.contradictions(conn, [c["id"] for c in usable])
             if r["attrs"].get("status") in ("suggested", "confirmed")]
    cells = []
    for combo in product(*(terms[a] for a in names)):
        cell = [c for c in usable if all(p.search(text[c["id"]]) for _, p in combo)]
        in_cell = {c["id"] for c in cell}
        contested = [{"a": a, "b": b} for a, b in pairs if a in in_cell and b in in_cell]
        works = sorted({w for c in cell for w in c["data"]["work_ids"]})
        cells.append({"terms": {a: label for a, (label, _) in zip(names, combo, strict=True)},
                      "status": _status(cell, bool(contested), labels), "claims": sorted(in_cell), "works": works,
                      "labels": {w: labels[w]["labels"] for w in works}, "contradictions": contested})
    counts = {s: sum(c["status"] == s for c in cells) for s in ("contested", "gap", "thin", "weak", "covered")}
    return {"project_id": project_id, "axes": {a: [label for label, _ in terms[a]] for a in names},
            "claims_used": len(usable), "claims_excluded_failed": len(ids) - len(usable), "cells": cells,
            "counts": counts}
