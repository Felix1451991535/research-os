"""Federated literature search over the registered BibliographicSources (K7), plus citation chaining.

Every run is logged (lit_search_runs: per-source returned/total/warnings/errors -> PRISMA "records identified");
one failing source never hides the others' results (its error is recorded). Network only through HostHttp with the
plugin's granted hosts; settings are the owner's stored settings (secrets backend-only). Egress (ADR-232): what a
source would receive (query text, filters, a chaining seed) passes the central egress gate first; content from a
local_only dataset (declared in Ctx.data_refs or detected) never reaches a source that is not `location = "local"`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from ...contracts.datasources import BibRecord, SearchPage, SearchQuery
from ...contracts.errors import ApiError
from ...contracts.http import HttpDenied, HttpUnavailable
from ...kernel.db import jdump, new_id, tx, utcnow
from ...kernel.events import emit
from .. import plugin_settings
from ..extensions import Extensions
from ..gateway import egress
from ..gateway.keystore import SecretStore
from ..knowledge.http_fetch import HostHttp
from . import candidates

Call = Callable[[Any, HostHttp, dict[str, Any]], SearchPage]


@dataclass(frozen=True)
class Ctx:
    ext: Extensions
    keystore: SecretStore
    actor: str
    project_id: str | None = None
    data_refs: tuple[egress.DataRef, ...] = ()  # datasets / answers whose content the query may carry


def _egress_block(conn: sqlite3.Connection, ctx: Ctx, name: str, src: Any, sent: list[str]) -> str | None:
    """The reason this source may not receive `sent` (None = allowed). Sources are remote unless they declare
    location = "local" (same default as model providers, ADR-159)."""
    if getattr(src, "location", "cloud") == "local":
        return None
    d = egress.check(conn, ctx.data_refs, sent)
    if not d.local_only:
        return None
    with tx(conn, join=True):
        emit(conn, "gateway.egress_blocked", actor=ctx.actor, project_id=ctx.project_id,
             data={"datasets": list(d.datasets), "how": d.how, "source": name})
    return egress.describe(d)


def _strings(value: Any) -> list[str]:
    """Every string in a (nested) dict / list / tuple: what an egress check must see of a structured payload."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [t for v in value for t in _strings(v)]
    return []


def _run_source(conn: sqlite3.Connection, ctx: Ctx, name: str, call: Call, sent: list[str]
                ) -> tuple[SearchPage | None, dict[str, Any]]:
    src = ctx.ext.sources[name]
    blocked = _egress_block(conn, ctx, name, src, sent)
    if blocked:
        return None, {"error": f"egress_blocked: {blocked}"}
    http = HostHttp(ctx.ext.granted_hosts(f"datasources:{name}", src.spec.hosts))
    try:
        settings = plugin_settings.effective(conn, ctx.keystore, ctx.ext, "datasources", name, None)
        page = call(src, http, settings)
    except ApiError as e:
        return None, {"error": f"{e.code}: {e.message}"}
    except (HttpDenied, HttpUnavailable, ValueError) as e:  # plugin-declared failure modes
        return None, {"error": f"{type(e).__name__}: {e}"}
    return page, {"returned": len(page.records), "total": page.total, "warnings": list(page.warnings),
                  "next_cursor": page.next_cursor}


def _record_run(conn: sqlite3.Connection, ctx: Ctx, kind: str, query: dict[str, Any], results: dict[str, Any],
                pages: dict[str, SearchPage], saved_search_id: str | None) -> dict[str, Any]:
    rid, ids, new = new_id("lsr"), [], []
    with tx(conn):
        for page in pages.values():
            for rec in page.records:
                cid, created = candidates.upsert(conn, rec, rid)
                if cid not in ids:
                    ids.append(cid)
                if created:
                    new.append(cid)
        conn.execute("INSERT INTO lit_search_runs(id,project_id,saved_search_id,kind,query,sources,per_source,"
                     "candidate_ids,new_candidate_ids,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (rid, ctx.project_id, saved_search_id, kind, jdump(query), jdump(sorted(results)),
                      jdump(results), jdump(ids), jdump(new), utcnow()))
        emit(conn, "literature.search_run", actor=ctx.actor, entity_type="lit_search_run", entity_id=rid,
             project_id=ctx.project_id, data={"kind": kind, "found": len(ids), "new": len(new),
                                              "errors": sorted(k for k, v in results.items() if "error" in v)})
    return {"id": rid, "kind": kind, "query": query, "per_source": results, "candidate_ids": ids,
            "new_candidate_ids": new}


def _rank(conn: sqlite3.Connection, ids: list[str], review_first: bool) -> list[dict[str, Any]]:
    cands = candidates.many(conn, ids)
    if review_first:  # review-first discovery: syntheses before primary studies, stable otherwise
        cands.sort(key=lambda c: 0 if c["record"].get("kind") == "review" else 1)
    return cands


def search(conn: sqlite3.Connection, ctx: Ctx, text: str, sources: list[str], *, filters: dict[str, str] | None = None,
           page_size: int = 25, review_first: bool = False, saved_search_id: str | None = None,
           kind: str = "search") -> dict[str, Any]:
    unknown = [s for s in sources if s not in ctx.ext.sources]
    if unknown or not sources:
        raise ApiError(422, "unknown_source", f"unknown or no sources: {unknown or sources}",
                       details={"available": sorted(ctx.ext.sources)})
    q = SearchQuery(text, filters or {}, page_size)
    results: dict[str, Any] = {}
    pages: dict[str, SearchPage] = {}
    for name in sources:
        page, results[name] = _run_source(conn, ctx, name, lambda s, h, st: s.search(q, h, st),
                                          [text, *(filters or {}).values()])
        if page is not None:
            pages[name] = page
    run = _record_run(conn, ctx, kind, {"text": text, "filters": filters or {}, "page_size": page_size}, results,
                      pages, saved_search_id)
    return {**run, "candidates": _rank(conn, run["candidate_ids"], review_first)}


def chain(conn: sqlite3.Connection, ctx: Ctx, record: BibRecord, direction: str, sources: list[str] | None = None,
          page_size: int = 25) -> dict[str, Any]:
    """direction: references (backward) | cited_by (forward). Uses every source with the capability."""
    if direction not in ("references", "cited_by"):
        raise ApiError(422, "bad_direction", "direction must be references or cited_by")
    capable = [n for n, s in ctx.ext.sources.items() if hasattr(s, direction)]
    use = [s for s in (sources or capable) if s in capable]
    if not use:
        raise ApiError(409, "no_citation_source", "no installed source supports citation chaining",
                       details={"capable": capable})
    results: dict[str, Any] = {}
    pages: dict[str, SearchPage] = {}
    seed = _strings(asdict(record))  # every field the source receives, not only title/ids/authors (Review #90)
    for name in use:
        page, results[name] = _run_source(
            conn, ctx, name, lambda s, h, st: getattr(s, direction)(record, h, st, None, page_size), seed)
        if page is not None:
            pages[name] = page
    run = _record_run(conn, ctx, direction, {"seed": asdict(record)}, results, pages, None)
    return {**run, "candidates": candidates.many(conn, run["candidate_ids"])}


def record_file_run(conn: sqlite3.Connection, ctx: Ctx, importer: str, filename: str, sha256: str,
                    records: list[BibRecord]) -> dict[str, Any]:
    """A parsed bibliography file becomes a logged run (PRISMA source = the importer) of candidates."""
    page = SearchPage(tuple(records))
    run = _record_run(conn, ctx, "search", {"file": filename, "sha256": sha256, "importer": importer},
                      {importer: {"returned": len(records), "total": len(records), "warnings": []}}, {importer: page},
                      None)
    return {**run, "candidates": candidates.many(conn, run["candidate_ids"])}


def _overview(cands: list[dict[str, Any]], reach: dict[str, set[str]]) -> dict[str, Any]:
    """Field-overview map computed only from logged results (no model): kinds, years, venues, and hubs = records
    reached from >= 2 seeds through 1-hop citation expansion."""

    def count(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in cands:
            v = c["record"].get(key)
            if v is not None:
                out[str(v)] = out.get(str(v), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    hubs = sorted((cid for cid, seeds in reach.items() if len(seeds) >= 2), key=lambda c: -len(reach[c]))
    return {"by_kind": count("kind"), "by_year": dict(sorted(count("year").items())),
            "top_venues": dict(list(count("venue").items())[:10]),
            "hubs": [{"candidate_id": c, "reached_from": len(reach[c])} for c in hubs[:20]]}


def orientation(conn: sqlite3.Connection, ctx: Ctx, text: str, sources: list[str], *, page_size: int = 20,
                seeds: int = 5) -> dict[str, Any]:
    """Orientation mode: reviews/surveys first, then 1-hop citation expansion (backward + forward) from the top
    reviews (or top hits if no review was found), then an overview map. Every step is a logged run."""
    base = search(conn, ctx, text, sources, page_size=page_size, review_first=True)
    reviews = [c for c in base["candidates"] if c["record"].get("kind") == "review"]
    seed_c = (reviews or base["candidates"])[:seeds]
    reach: dict[str, set[str]] = {}
    runs, notes = [base["id"]], []
    for s in seed_c:
        for direction in ("references", "cited_by"):
            try:
                r = chain(conn, ctx, candidates.record_of(s), direction, None, page_size)
            except ApiError as e:
                notes.append(e.message)
                break
            runs.append(r["id"])
            for cid in r["candidate_ids"]:
                reach.setdefault(cid, set()).add(s["id"])
    ids = list(dict.fromkeys([*(c["id"] for c in base["candidates"]), *reach]))  # ranked (reviews first)
    cands = candidates.many(conn, ids)
    return {"runs": runs, "reviews": [c["id"] for c in reviews], "seeds": [c["id"] for c in seed_c],
            "map": _overview(cands, reach), "notes": sorted(set(notes)), "candidates": cands}
