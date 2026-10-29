"""Deep research report (K8 Doc kind `report`). Every sentence carries its Evidence placeholders
(`{{evidence:<id>}}`) and the citekeys of the works they come from (`{{cite:<key>}}`), so M6 export gates can
resolve them; numbers in method sections come from the logs (PRISMA), never from a model. Fixed text comes from the
core catalog in the run's `language` setting (ADR-243; en default); sub-questions, statements and titles are data."""

from __future__ import annotations

import sqlite3
from typing import Any

from ...kernel.db import jload, row, tx
from ...sdk.texts import DEFAULT_LANGUAGE, Bound
from ..content import docs
from ..content.texts import CORE
from ..literature import citekeys, screening
from . import claims


def _answer_section(conn: sqlite3.Connection, T: Bound, i: int, a: dict[str, Any]) -> list[str]:
    ans = row(conn.execute("SELECT status, body, trust_composition FROM answers WHERE id=?", (a["answer_id"],))) or {}
    body = jload(ans.get("body")) or {}
    out = [f"## {i + 1}. {a['sub_question']}", ""]
    sents = [s for s in body.get("sentences") or [] if s.get("evidence_ids")]
    if not sents:
        out += [T("dr_insufficient"), ""]
        return out
    for s in sents:
        out.append(f"- {citekeys.cited_markdown(conn, s['text'], list(s['evidence_ids']))} *({s.get('kind')})*")
    dropped = len(body.get("dropped") or [])
    trust = ", ".join(f"{k} {_n(v)}" for k, v in (jload(ans.get("trust_composition")) or {}).items())
    out += ["", T("dr_trust", trust=trust, n=_n(dropped)), ""]
    return out


def _n(v: Any) -> str:
    """Values computed by the system from its own logs at generation time (not model/author numbers): marked as
    literals for the M6 number gate."""
    return f"{{{{lit:{v}}}}}"


def _prisma(T: Bound, p: dict[str, Any]) -> list[str]:
    by_src = ", ".join(f"{k} {_n(v)}" for k, v in p["identified_by_source"].items())
    reasons = ", ".join(f"{k} {_n(v)}" for k, v in p["excluded_reasons"].items())
    return [f"## {T('dr_method')}", "",
            T("dr_identified", n=_n(p["records_identified"]), by_source=by_src,
              duplicates=_n(p["duplicates_removed"])),
            T("dr_screened", n=_n(p["records_screened"]), excluded=_n(p["records_excluded"]), reasons=reasons),
            T("dr_fulltext"), T("dr_suggestions_only"), ""]


def _contradictions(conn: sqlite3.Connection, T: Bound, claim_ids: list[str], flags: dict[str, Any]) -> list[str]:
    out = [f"## {T('dr_contradictions')}", ""]
    pairs = claims.contradictions(conn, claim_ids)
    by_id = {c["id"]: c for c in claims.get_many(conn, claim_ids)}
    for p in pairs:
        a, b = by_id.get(p["src"]), by_id.get(p["dst"])
        if a and b:
            out.append(f"- **{p['attrs'].get('aspect')}** ({p['attrs'].get('status')}): \"{a['data']['text']}\" vs "
                       f"\"{b['data']['text']}\"")
    if not pairs:  # the error / note are system text kept as recorded (protocol values)
        out.append(T("dr_none_flagged") + (T("dr_check_not_run", error=flags["error"]) if flags.get("error") else "")
                   + (f" ({flags['note']})" if flags.get("note") else ""))
    return [*out, ""]


def _sources(conn: sqlite3.Connection, T: Bound, works: list[str]) -> list[str]:
    out = [f"## {T('dr_sources')}", ""]
    for w in works:
        r = row(conn.execute("SELECT title, year, retraction FROM works WHERE id=?", (w,))) or {}
        full = row(conn.execute("SELECT 1 FROM document_versions WHERE work_id=? AND parse_state IN "
                                "('parsed','partial')", (w,)))
        ret = (jload(r.get("retraction")) or {}).get("retracted")
        title = _n(str(r.get("title") or "").replace("{", "(").replace("}", ")"))
        out.append(f"- {{{{cite:{citekeys.assign(conn, w)}}}}} {title} ({_n(r.get('year') or 'n.d.')})"
                   f"{T('dr_retracted') if ret else ''}{'' if full else T('dr_metadata_only')}")
    return [*out, ""]


def write(conn: sqlite3.Connection, svc: Any, r: dict[str, Any], actor: str) -> dict[str, Any]:
    d = r["data"]
    T = CORE.lang(r["settings"].get("language", DEFAULT_LANGUAGE))  # runs created before ADR-243: en
    title = T("dr_title", question=r["question"])
    lines = [f"# {title}", "", T("dr_intro"), ""]
    lines += _prisma(T, screening.prisma(conn, r["review_id"]))
    for i, a in enumerate(d.get("answers") or []):
        lines += _answer_section(conn, T, i, a)
    lines += _contradictions(conn, T, d.get("claims") or [], d.get("contradictions") or {})
    lines += _sources(conn, T, d.get("works") or [])
    lines += [f"## {T('dr_limitations')}", "", T("dr_limit_sources"), T("dr_limit_trust"), ""]
    with tx(conn):
        return docs.create_in_tx(conn, svc.doc_kinds, r["project_id"], "report", title, "\n".join(lines), actor)
