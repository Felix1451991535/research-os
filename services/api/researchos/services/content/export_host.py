"""Host side of K8 exports: DB-backed ExportContext, snapshot builders, confined + hashed output publication."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ...contracts.datasources import BibRecord
from ...contracts.errors import ApiError, not_found
from ...contracts.exporters import (
    ArtifactRef,
    Block,
    CitationEntry,
    EvidenceRef,
    Exporter,
    MetricSpan,
    MetricValue,
    ReportSnapshot,
    TextSpan,
)
from ...kernel.db import jload, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from ...platform.paths import has_reparse_point
from ...sdk.texts import DEFAULT_LANGUAGE, Bound, export_language, snapshot_language
from .citekey_index import lookup_work
from .texts import CORE


@dataclass
class DbExportContext:
    conn: sqlite3.Connection
    data_dir: Path
    out_dir: Path
    mode: Literal["draft", "final"] = "final"

    def citation(self, citekey: str) -> CitationEntry | None:
        w = lookup_work(self.conn, citekey)
        if w is None:
            return None
        ids = tuple(
            (r["scheme"], r["value"])
            for r in rows(
                self.conn.execute(
                    "SELECT scheme, value FROM identifiers WHERE work_id=? AND scheme IN ('doi','pmid','arxiv')",
                    (w["id"],),
                )
            )
        )
        meta = jload(w["metadata"]) or {}
        rec = BibRecord(
            source="library",
            source_id=w["id"],
            title=w["title"],
            authors=tuple(jload(w["authors"]) or []),
            year=w["year"],
            venue=w["venue"],
            identifiers=ids,
            url=meta.get("url"),
        )
        ev = tuple(
            EvidenceRef(e["id"], e["version_id"], e["page_index"], e["quote"], e["trust"])
            for e in rows(
                self.conn.execute(
                    "SELECT e.* FROM evidence e JOIN document_versions v ON v.version_id=e.version_id "
                    "WHERE v.work_id=? ORDER BY e.created_at LIMIT 50",
                    (w["id"],),
                )
            )
        )
        return CitationEntry(citekey, rec, ev)

    def metric(self, run_id: str, name: str, split: str) -> MetricValue | None:
        m = row(
            self.conn.execute("SELECT * FROM run_metrics WHERE run_id=? AND name=? AND split=?", (run_id, name, split))
        )
        if m is None or m["value"] is None:
            return None
        return MetricValue(float(m["value"]), m["unit"], m["verification"])

    def artifact(self, artifact_id: str) -> ArtifactRef | None:
        a = row(self.conn.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)))
        if a is None:
            return None
        p = self.data_dir / a["path"]
        mime = MIME.get(p.suffix.lower(), "application/octet-stream")  # exporters only embed images (ADR-165)
        return ArtifactRef(a["id"], mime, a["sha256"], a["run_id"], p) if p.is_file() else None


MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _figures(conn: sqlite3.Connection, run_id: str, T: Bound) -> list[Block]:
    q = "SELECT id, path FROM artifacts WHERE run_id=? AND kind='figure' ORDER BY path"
    figs = rows(conn.execute(q, (run_id,)))
    if not figs:
        return []
    out = [Block("heading", (TextSpan(T("figures")),))]
    for i, f in enumerate(figs, start=1):
        name = f["path"].rsplit("/", 1)[-1]
        out.append(Block("figure", artifact_id=f["id"], caption=(TextSpan(T("figure_caption", n=i, name=name)),)))
    return out


def _predictions(plan: dict[str, Any], T: Bound) -> list[Block]:
    if not plan.get("predictions"):
        return []
    lines = [T("prediction_prior", metric=p["metric"], expected=p["expected"], prior=p["prior"]) if "prior" in p
             else T("prediction", metric=p["metric"], expected=p["expected"]) for p in plan["predictions"]]
    return [Block("heading", (TextSpan(T("predictions")),)), *(Block("list", (TextSpan(x),)) for x in lines)]


def _provenance(m: dict[str, Any], T: Bound) -> list[Block]:
    t = m["template"]
    lines = (T("prov_template", id=t["id"], version=t["version"], hash=t["hash"][:12]),
             T("prov_dataset", dataset=m.get("dataset")), T("prov_split", split_hash=m.get("split_hash")),
             T("prov_seed", seed=m["seed"], params=m["params"]),
             T("prov_git", git=m["git"], env_lock=m.get("env_lock")),
             T("prov_device", device=m["device"]), T("prov_python", python=m["python"], platform=m["platform"]))
    return [Block("heading", (TextSpan(T("provenance")),)), *(Block("list", (TextSpan(x),)) for x in lines)]


def snapshot_from_run(conn: sqlite3.Connection, data_dir: Path, run_id: str, language: str = DEFAULT_LANGUAGE
                      ) -> ReportSnapshot:
    """The run report in `language` (fixed text from the core catalog; data and protocol values as recorded)."""
    T = CORE.lang(language)
    r = row(
        conn.execute(
            "SELECT r.*, t.state AS task_state FROM runs r JOIN tasks t ON t.id=r.task_id WHERE r.id=?", (run_id,)
        )
    )
    if r is None or not r["run_dir"]:
        raise not_found("run")
    m = jload((data_dir / r["run_dir"] / "run.json").read_text(encoding="utf-8"))
    rev = row(
        conn.execute(
            "SELECT content FROM plan_revisions WHERE plan_id=? AND revision=?",
            (m["plan"]["id"], m["plan"]["revision"]),
        )
    )
    plan: dict[str, Any] = jload(rev["content"]) if rev else {}
    summary = T("run_summary", plan_id=m["plan"]["id"], revision=m["plan"]["revision"], hash=m["plan"]["hash"][:12],
                stage=m["stage"], state=r["task_state"])
    b: list[Block] = [
        Block("paragraph", (TextSpan(summary),)),
        Block("heading", (TextSpan(T("hypothesis")),)),
        Block("paragraph", (TextSpan(plan.get("hypothesis", "")),)),
        *_predictions(plan, T),
    ]
    metrics = rows(conn.execute("SELECT * FROM run_metrics WHERE run_id=? ORDER BY name", (run_id,)))
    b.append(Block("heading", (TextSpan(T("metrics")),)))
    header = ((TextSpan(T("col_metric")),), (TextSpan(T("col_split")),), (TextSpan(T("col_value")),))
    b.append(Block("table", rows=(header, *(((TextSpan(x["name"]),), (TextSpan(x["split"]),),
                                             (MetricSpan(run_id, x["name"], x["split"]),)) for x in metrics))))
    b += _figures(conn, run_id, T)
    sig = rows(conn.execute("SELECT * FROM health_signals WHERE run_id=? ORDER BY created_at", (run_id,)))
    b.append(Block("heading", (TextSpan(T("health_signals")),)))
    b += [Block("list", (TextSpan(f"{s['severity']} {s['detector']}: {s['rule']}"),)) for s in sig] or [
        Block("list", (TextSpan(T("none")),))
    ]
    b += _provenance(m, T)
    return ReportSnapshot(run_id, 1, T("run_title", run_id=run_id), snapshot_language(language), tuple(b), "report")


def language_of(options: dict[str, Any]) -> str:
    """The export's `language` option (en default); 422 `bad_language` otherwise."""
    try:
        return export_language(options)
    except ValueError as e:
        raise ApiError(422, "bad_language", str(e)) from e


def publish(
    conn: sqlite3.Connection,
    data_dir: Path,
    exporter: Exporter,
    snapshot: ReportSnapshot,
    mode: Literal["draft", "final"],
    options: dict[str, Any],
    actor: str,
    *, export_id: str | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    if snapshot.kind not in exporter.spec.doc_kinds:
        raise ApiError(422, "unsupported_doc_kind", f"{exporter.spec.name} does not export {snapshot.kind}")
    language_of(options)  # every exporter gets a valid `language` option (ADR-234)
    eid = export_id or new_id("exp")
    out = data_dir / "exports" / f"{snapshot.doc_id}-{exporter.spec.name}-{eid}"
    out.mkdir(parents=True, exist_ok=True)
    res = exporter.export(snapshot, DbExportContext(conn, data_dir, out, mode), options)
    files = []
    for f in res.files:
        p = (out / f.rel_path).resolve()
        if not p.is_relative_to(out.resolve()) or not p.is_file():
            raise ApiError(
                500,
                "exporter_contract_violation",
                f"{exporter.spec.name} produced {f.rel_path} outside its output directory or did not write it",
            )
        files.append(
            {
                "path": p.relative_to(data_dir).as_posix(),
                "mime": f.mime,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
        )
    errors = [d for d in res.diagnostics if d.severity == "error"]
    status = "failed" if errors and mode == "final" else "ok"
    with tx(conn, join=True):
        emit(
            conn,
            "export.created",
            actor=actor,
            entity_type="export",
            entity_id=eid,
            project_id=project_id,
            data={
                "exporter": exporter.spec.name,
                "doc": snapshot.doc_id,
                "status": status,
                "files": files,
                "diagnostics": [d.__dict__ for d in res.diagnostics],
                "at": utcnow(),
            },
        )
    return {"id": eid, "status": status, "files": files, "diagnostics": [d.__dict__ for d in res.diagnostics]}


def run_project(conn: sqlite3.Connection, run_id: str) -> str | None:
    r = row(conn.execute("SELECT p.project_id FROM runs r JOIN tasks t ON t.id=r.task_id "
                         "JOIN plans p ON p.id=json_extract(t.payload, '$.plan_id') WHERE r.id=?", (run_id,)))
    return r["project_id"] if r else None


def export_download(conn: sqlite3.Connection, data_dir: Path, export_id: str, name: str) -> Path:
    """A file of a REGISTERED, non-failed export (the `export.created` record), by export id and file name, whose
    bytes still match the recorded hash (review #56). Anything else is 404 without saying why."""
    ev = row(conn.execute("SELECT data FROM events WHERE kind='export.created' AND entity_id=?", (export_id,)))
    data: dict[str, Any] = jload(ev["data"]) if ev else {}
    entry = next((f for f in data.get("files", []) if Path(f["path"]).name == name), None)
    if not ev or data.get("status") == "failed" or entry is None:
        raise not_found("export file")
    root, candidate = (data_dir / "exports").resolve(), data_dir / str(entry["path"])
    if has_reparse_point(candidate, data_dir) or not candidate.is_file():
        raise not_found("export file")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or hashlib.sha256(resolved.read_bytes()).hexdigest() != entry["sha256"]:
        raise not_found("export file")
    return resolved
