"""Open-access PDFs (opt-in). A PDF is stored only if the owner enabled downloads, the location states an explicit
open licence, the bytes are a PDF and the URL is https on a public host (HostHttp SSRF guard, redirects re-checked).
Otherwise the work stays metadata-only and the reason is returned."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from ...contracts.datasources import OaLocation
from ...contracts.http import HttpDenied, HttpPolicy, HttpUnavailable
from ...kernel.db import jdump, jload, row, tx, utcnow
from ...kernel.events import emit
from .. import plugin_settings
from ..knowledge.http_fetch import HostHttp
from ..knowledge.writer import DbKnowledgeWriter
from .discovery import Ctx

OPEN_LICENSES = {"CC-BY", "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0"}
DEFAULTS = {"pdf_download": False, "retraction_checks": True}


def lit_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    r = row(conn.execute("SELECT value_json FROM settings_kv WHERE key='literature'"))
    return {**DEFAULTS, **(jload(r["value_json"]) if r else {})}


def set_lit_settings(conn: sqlite3.Connection, values: dict[str, Any], actor: str) -> dict[str, Any]:
    new = {**lit_settings(conn), **{k: bool(v) for k, v in values.items() if k in DEFAULTS}}
    with tx(conn):
        conn.execute("INSERT INTO settings_kv(key,value_json,updated_at) VALUES ('literature',?,?) ON CONFLICT(key) "
                     "DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                     (jdump(new), utcnow()))
        emit(conn, "literature.settings_changed", actor=actor, entity_type="settings", entity_id="literature", data=new)
    return new


def locate(conn: sqlite3.Connection, ctx: Ctx, doi: str) -> tuple[OaLocation | None, dict[str, str]]:
    errors: dict[str, str] = {}
    for name, res in sorted(ctx.ext.oa_resolvers.items()):
        http = HostHttp(ctx.ext.granted_hosts(f"datasources:{name}", res.spec.hosts))
        try:
            loc = res.resolve(doi, http, plugin_settings.effective(conn, ctx.keystore, ctx.ext, "datasources", name,
                                                                   None))
        except Exception as e:  # recorded; next resolver  # noqa: BLE001
            errors[name] = f"{type(e).__name__}: {e}"
            continue
        if loc is not None:
            return loc, errors
    return None, errors


def fetch_pdf(conn: sqlite3.Connection, ctx: Ctx, writer: DbKnowledgeWriter, work_id: str, loc: OaLocation
              ) -> dict[str, Any]:
    """Returns {"version_id"} or {"skipped": reason}. Never stores a PDF without an explicit open licence."""
    if not lit_settings(conn)["pdf_download"]:
        return {"skipped": "PDF download is off (owner setting literature.pdf_download)"}
    if loc.license not in OPEN_LICENSES or not loc.license_source:
        return {"skipped": f"no explicit open licence ({loc.license or 'unknown'}); metadata only"}
    host = (urlsplit(loc.pdf_url).hostname or "").lower()
    if urlsplit(loc.pdf_url).scheme != "https" or not host:
        return {"skipped": "PDF URL is not https"}
    try:
        resp = HostHttp((host,), HttpPolicy(max_bytes=50_000_000, cache_ttl_s=0)).get(loc.pdf_url)
    except (HttpDenied, HttpUnavailable) as e:
        return {"skipped": f"download refused or failed: {e}"}
    if resp.status != 200 or not resp.body.startswith(b"%PDF-"):
        return {"skipped": f"not a PDF response (HTTP {resp.status})"}
    vid = writer.attach_original(work_id, resp.body, f"{work_id}.pdf", license=loc.license,
                                 idempotency_key=f"oa:{work_id}:{loc.pdf_url}")
    with tx(conn):
        emit(conn, "work.oa_pdf_stored", actor=ctx.actor, entity_type="work", entity_id=work_id,
             data={"version_id": vid, "location": asdict(loc)})
    return {"version_id": vid, "location": asdict(loc)}
