"""Retraction checks (ARCHITECTURE §5.2): a retracted source sets every Evidence row from its documents to `failed`
and raises an alert listing affected answers. Status per work = combination over all RetractionSources:
any True -> retracted; else any False -> not retracted; else unknown (never assumed fine)."""

from __future__ import annotations

import sqlite3
from typing import Any

from ...contracts.datasources import IdScheme
from ...kernel.db import jdump, jload, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from .. import plugin_settings
from ..knowledge.http_fetch import HostHttp
from .discovery import Ctx

BATCH = 50


def _sources(ctx: Ctx) -> list[str]:
    return sorted(n for n, s in ctx.ext.sources.items() if hasattr(s, "retraction_status"))


def _query(conn: sqlite3.Connection, ctx: Ctx, dois: list[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    status: dict[str, dict[str, Any]] = {d: {"retracted": None, "source": None, "notice_url": None} for d in dois}
    errors: dict[str, str] = {}
    for name in _sources(ctx):
        src = ctx.ext.sources[name]
        http = HostHttp(ctx.ext.granted_hosts(f"datasources:{name}", src.spec.hosts))
        settings = plugin_settings.effective(conn, ctx.keystore, ctx.ext, "datasources", name, None)
        for i in range(0, len(dois), BATCH):
            ids: tuple[tuple[IdScheme, str], ...] = tuple(("doi", d) for d in dois[i : i + BATCH])
            try:
                res = src.retraction_status(ids, http, settings)
            except Exception as e:  # plugin/network failure: recorded, others continue  # noqa: BLE001
                errors[name] = f"{type(e).__name__}: {e}"
                break
            for st in res:
                cur = status[st.identifier[1]]
                if st.retracted is True or (st.retracted is False and cur["retracted"] is None):
                    status[st.identifier[1]] = {"retracted": st.retracted, "source": name,
                                                "notice_url": st.notice_url}
    return status, errors


def _fail_evidence(conn: sqlite3.Connection, work_id: str) -> tuple[int, list[str]]:
    ev = [r["id"] for r in rows(conn.execute(
        "SELECT e.id FROM evidence e JOIN document_versions v ON v.version_id=e.version_id WHERE v.work_id=? "
        "AND e.trust!='failed'", (work_id,)))]
    if ev:
        conn.execute(f"UPDATE evidence SET trust='failed' WHERE id IN ({','.join('?' * len(ev))})", ev)
    answers = sorted({r["id"] for e in ev for r in rows(conn.execute(
        "SELECT id FROM answers WHERE body LIKE ?", (f"%{e}%",)))})
    return len(ev), answers


def check_works(conn: sqlite3.Connection, ctx: Ctx, work_ids: list[str] | None = None) -> dict[str, Any]:
    q = "SELECT work_id, value FROM identifiers WHERE scheme='doi'"
    pairs = rows(conn.execute(q + (f" AND work_id IN ({','.join('?' * len(work_ids))})" if work_ids else ""),
                              work_ids or []))
    if not _sources(ctx):
        return {"checked": 0, "retracted": [], "errors": {"_": "no installed source reports retractions"}}
    status, errors = _query(conn, ctx, sorted({p["value"] for p in pairs}))
    newly: list[dict[str, Any]] = []
    now = utcnow()
    with tx(conn):
        for p in pairs:
            st = {**status[p["value"]], "checked_at": now}
            old = jload((row(conn.execute("SELECT retraction FROM works WHERE id=?", (p["work_id"],))) or {})
                        .get("retraction"))
            conn.execute("UPDATE works SET retraction=? WHERE id=?", (jdump(st), p["work_id"]))
            conn.execute("UPDATE lit_candidates SET retracted=?, retraction_source=?, retraction_checked_at=? "
                         "WHERE dedupe_key=?", (None if st["retracted"] is None else int(st["retracted"]),
                                                st["source"], now, f"doi:{p['value']}"))
            if st["retracted"] and not (old or {}).get("retracted"):
                n_ev, answers = _fail_evidence(conn, p["work_id"])
                data = {"work_id": p["work_id"], "doi": p["value"], "source": st["source"],
                        "notice_url": st["notice_url"], "evidence_failed": n_ev, "affected_answers": answers}
                conn.execute("INSERT INTO alerts(id,kind,subject_id,data,created_at) VALUES (?,?,?,?,?)",
                             (new_id("alert"), "retraction", p["work_id"], jdump(data), now))
                emit(conn, "work.retracted", actor=ctx.actor, entity_type="work", entity_id=p["work_id"], data=data)
                newly.append(data)
    return {"checked": len(pairs), "retracted": newly, "errors": errors,
            "unknown": sorted(p["value"] for p in pairs if status[p["value"]]["retracted"] is None)}
