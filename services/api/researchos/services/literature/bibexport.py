"""Library export as BibTeX, RIS or CSL-JSON (citekeys assigned on demand). Only stored metadata is written;
unknown fields are omitted (never guessed). Retracted works carry an explicit RETRACTED note."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ...kernel.db import jload, rows
from . import citekeys

BIB_TYPES = {"article": "article", "review": "article", "preprint": "misc", "thesis": "phdthesis", "book": "book",
             "report": "techreport"}
RIS_TYPES = {"article": "JOUR", "review": "JOUR", "preprint": "UNPB", "thesis": "THES", "book": "BOOK",
             "report": "RPRT"}
CSL_TYPES = {"article": "article-journal", "review": "article-journal", "preprint": "article", "thesis": "thesis",
             "book": "book", "report": "report"}
_BIB_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
            "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def _works(conn: sqlite3.Connection, work_ids: list[str] | None) -> list[dict[str, Any]]:
    q = "SELECT * FROM works" + (f" WHERE id IN ({','.join('?' * len(work_ids))})" if work_ids else "")
    out = []
    for w in rows(conn.execute(q + " ORDER BY created_at", work_ids or [])):
        ids = {r["scheme"]: r["value"] for r in rows(conn.execute(
            "SELECT scheme, value FROM identifiers WHERE work_id=?", (w["id"],)))}
        out.append({**w, "authors": jload(w["authors"]) or [], "metadata": jload(w["metadata"]) or {},
                    "retraction": jload(w["retraction"]) or {}, "ids": ids,
                    "citekey": citekeys.assign(conn, w["id"])})
    return out


def _esc(s: str) -> str:
    return "".join(_BIB_ESC.get(c, c) for c in s if ord(c) >= 32)


def bibtex(conn: sqlite3.Connection, work_ids: list[str] | None = None) -> str:
    entries = []
    for w in _works(conn, work_ids):
        f: dict[str, str | None] = {
            "author": " and ".join(w["authors"]) or None, "title": w["title"] or None,
            "journal" if w["kind"] in ("article", "review") else "howpublished": w["venue"],
            "year": str(w["year"]) if w["year"] else None, "doi": w["ids"].get("doi"),
            "url": w["metadata"].get("url"), "eprint": w["ids"].get("arxiv"),
            "archiveprefix": "arXiv" if w["ids"].get("arxiv") else None, "pmid": w["ids"].get("pmid"),
            "note": "RETRACTED" if w["retraction"].get("retracted") else None,
        }
        body = ",\n".join(f"  {k} = {{{_esc(v)}}}" for k, v in f.items() if v)
        entries.append(f"@{BIB_TYPES.get(w['kind'], 'misc')}{{{w['citekey']},\n{body}\n}}")
    return "\n\n".join(entries) + ("\n" if entries else "")


def ris(conn: sqlite3.Connection, work_ids: list[str] | None = None) -> str:
    out = []
    for w in _works(conn, work_ids):
        lines = [("TY", RIS_TYPES.get(w["kind"], "GEN")), ("ID", w["citekey"]), ("TI", w["title"])]
        lines += [("AU", a) for a in w["authors"]]
        lines += [(t, v) for t, v in (("PY", str(w["year"]) if w["year"] else None), ("JO", w["venue"]),
                                      ("DO", w["ids"].get("doi")), ("UR", w["metadata"].get("url")),
                                      ("AB", w["metadata"].get("abstract")),
                                      ("N1", "RETRACTED" if w["retraction"].get("retracted") else None)) if v]
        out.append("\n".join(f"{t}  - {' '.join(str(v).split())}" for t, v in lines if v) + "\nER  - ")
    return "\n\n".join(out) + ("\n" if out else "")


def csl_json(conn: sqlite3.Connection, work_ids: list[str] | None = None) -> str:
    items = []
    for w in _works(conn, work_ids):
        it: dict[str, Any] = {"id": w["citekey"], "type": CSL_TYPES.get(w["kind"], "document"), "title": w["title"],
                              "author": [{"literal": a} for a in w["authors"]]}
        if w["year"]:
            it["issued"] = {"date-parts": [[w["year"]]]}
        for k, v in (("container-title", w["venue"]), ("DOI", w["ids"].get("doi")), ("URL", w["metadata"].get("url")),
                     ("PMID", w["ids"].get("pmid")), ("abstract", w["metadata"].get("abstract"))):
            if v:
                it[k] = v
        if w["retraction"].get("retracted"):
            it["note"] = "RETRACTED"
        items.append(it)
    return json.dumps(items, ensure_ascii=False, indent=1) + "\n"


FORMATS = {"bibtex": (bibtex, "application/x-bibtex", "bib"), "ris": (ris, "application/x-research-info-systems",
                                                                         "ris"),
           "csl-json": (csl_json, "application/vnd.citationstyles.csl+json", "json")}
