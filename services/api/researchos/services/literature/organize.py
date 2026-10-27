"""Library organisation (ARCHITECTURE §7 library workflow) -> a K4 EffectPlan, copy-by-default.

scan a read root (PDFs only; others noted) -> hash (same bytes = one file) -> import into the CAS (internal write)
-> identify: DOI/arXiv from the first two pages, matched against the library first, then the configured sources by
exact identifier (never by title) -> citekey -> `<library>/<year|nd>/<citekey>.pdf` (`-v2`.. for other versions of
the same work); unidentified -> `<library>/_unsorted/<original name>`. Nothing outside data_dir changes until the
owner approves and applies the plan."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...contracts.datasources import BibRecord
from ...contracts.errors import ApiError
from ...contracts.knowledge import ParseError
from ...kernel.db import Row, row
from ...platform.paths import PathRuleError, check_component
from ..effects import fsops, plan, roots
from ..knowledge.importer import import_document
from ..knowledge.isolated import parse_isolated
from ..knowledge.metadata import find_identifiers
from ..knowledge.writer import DbKnowledgeWriter
from . import candidates, citekeys, discovery

MAX_FILES = 2000


@dataclass
class Found:
    path: Path
    size: int
    sha: str
    ids: list[tuple[str, str]] = field(default_factory=list)
    work_id: str | None = None
    record: BibRecord | None = None
    target: str | None = None


def scan(src_root: Path, notes: list[str]) -> list[Found]:
    files: list[Found] = []
    by_sha: dict[str, Path] = {}
    for p in sorted(x for x in src_root.rglob("*") if x.is_file() and not x.name.startswith(".")):
        if len(files) >= MAX_FILES:
            notes.append(f"stopped after {MAX_FILES} files")
            break
        with p.open("rb") as fh:
            head = fh.read(5)
        if head != b"%PDF-":
            notes.append(f"skipped (not a PDF): {p}")
            continue
        sha = fsops.sha256_file(p)
        if sha in by_sha:
            notes.append(f"duplicate bytes, kept once: {p} == {by_sha[sha]}")
            continue
        by_sha[sha] = p
        files.append(Found(p, p.stat().st_size, sha))
    return files


def _ids(f: Found, home: Path, notes: list[str]) -> None:
    try:
        res = parse_isolated(f.path, max_pages=2, timeout_s=30, home=home)
    except ParseError as e:
        notes.append(f"could not read text ({e.code}): {f.path}")
        return
    text = "\n".join(p.text for p in res.pages)
    f.ids = find_identifiers(text)


def _lookup(conn: sqlite3.Connection, ctx: discovery.Ctx, scheme: str, value: str
            ) -> tuple[str | None, BibRecord | None]:
    w = row(conn.execute("SELECT work_id FROM identifiers WHERE scheme=? AND value=?", (scheme, value)))
    if w is not None:
        return str(w["work_id"]), None
    if not ctx.ext.sources:
        return None, None
    res = discovery.search(conn, ctx, value, sorted(ctx.ext.sources), page_size=5)
    hit = next((c for c in res["candidates"] if c["dedupe_key"] == f"{scheme}:{value}"), None)
    return None, candidates.record_of(hit) if hit else None


def _identify(conn: sqlite3.Connection, ctx: discovery.Ctx, writer: DbKnowledgeWriter, f: Found) -> None:
    for scheme, value in f.ids:
        wid, rec = _lookup(conn, ctx, scheme, value)
        if wid or rec:
            f.work_id = wid or writer.upsert_work(rec, idempotency_key=f"organize:{scheme}:{value}")[0]  # type: ignore[arg-type]
            f.record = rec
            break
    data = f.path.read_bytes()
    if f.work_id:
        writer.attach_original(f.work_id, data, f.path.name, license=None, idempotency_key=f"organize:pdf:{f.sha}")
    else:
        import_document(conn, writer.blobs, writer.kinds, data=data, filename=f.path.name,
                        project_id=writer.project_id, actor=writer.actor, max_bytes=writer.max_bytes)


def _safe_name(name: str) -> str:
    """Windows-valid file name: invalid characters -> '_', trailing dots/spaces stripped, reserved device names
    prefixed with '_'."""
    cand = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).rstrip(". ") or "file.pdf"
    try:
        check_component(cand)
        return cand
    except PathRuleError:
        return f"_{cand}"


def _targets(conn: sqlite3.Connection, files: list[Found], lib: Path) -> None:
    used: dict[str, int] = {}
    for f in files:
        if f.work_id is None:
            f.target = str(lib / "_unsorted" / _safe_name(f.path.name))
            continue
        key = citekeys.assign(conn, f.work_id)
        w: Row = row(conn.execute("SELECT year FROM works WHERE id=?", (f.work_id,))) or {}
        n = used[key] = used.get(key, 0) + 1
        f.target = str(lib / (str(w.get("year")) if w.get("year") else "nd") / f"{key}{'' if n == 1 else f'-v{n}'}.pdf")
    seen: dict[str, int] = {}
    for f in files:  # identical unsorted names from different folders
        assert f.target is not None
        k = f.target.casefold()
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            p = Path(f.target)
            f.target = str(p.with_name(f"{p.stem}-{seen[k]}{p.suffix}"))


def _ops(files: list[Found], lib: Path, mode: str) -> list[dict[str, Any]]:
    dirs = sorted({str(Path(f.target).parent) for f in files if f.target}, key=lambda d: (d.count("/"), d))
    ops: list[dict[str, Any]] = [{"op": "mkdir", "path": d} for d in dirs if not Path(d).is_dir()]
    ops += [{"op": mode, "src": str(f.path), "dst": f.target, "sha256": f.sha, "size": f.size} for f in files
            if f.target and not Path(f.target).exists()]
    return ops


def propose(conn: sqlite3.Connection, ctx: discovery.Ctx, writer: DbKnowledgeWriter, home: Path, *, source_dir: str,
            library_dir: str, mode: str = "copy") -> dict[str, Any]:
    if mode not in ("copy", "move"):
        raise ApiError(422, "bad_mode", "mode must be copy (default) or move")
    src = roots.resolve(conn, source_dir, "write" if mode == "move" else "read", must_exist=True)
    lib = roots.resolve(conn, library_dir, "write", must_exist=True)
    notes: list[str] = []
    files = scan(src, notes)
    for f in files:
        _ids(f, home, notes)
        _identify(conn, ctx, writer, f)
    _targets(conn, files, lib)
    ops = _ops(files, lib, mode)
    unsorted = sum(1 for f in files if f.work_id is None)
    notes.append(f"{len(files)} PDFs: {len(files) - unsorted} identified, {unsorted} to _unsorted (review queue)")
    if not ops:
        return {"plan": None, "notes": notes}
    p = plan.propose(conn, kind="library_organize", title=f"Organise {src} -> {lib} ({mode})", ops=ops,
                     actor=ctx.actor, notes=notes, project_id=ctx.project_id)
    return {"plan": p, "notes": notes, "files": [{"src": str(f.path), "target": f.target, "work_id": f.work_id,
                                                  "ids": f.ids} for f in files]}
