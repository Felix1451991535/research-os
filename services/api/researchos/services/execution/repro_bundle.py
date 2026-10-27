"""Reproducible bundle of a plan (V1 acceptance item 7, ADR-192; design reference: "everything for a run in one
directory", AI-Scientist-v3 - idea only). One zip per request, published as an export record (hash-checked
download through the existing export route):

- `plan/`: every revision (content + hash) and the approvals recorded for them;
- `runs/<run_id>/`: every run of the plan, FAILED ONES INCLUDED, allow-listed (review #72): top-level JSON / JSONL /
  log records up to 5 MB (run.json with params, seed, template/dataset/split hashes, git commit, env lock hash;
  metrics, model card, ...), predictions/*.csv and figure images; anything else (inputs, home, stray files that may
  be data) is left out and listed with the reason; plus the task state and error;
- `template/<id>/`: the template source, only if the installed copy still has the hash the runs used;
- `dataset/`: the dataset manifest (source, licence, egress, file hashes, split) and the build settings - never the
  data itself: rebuild it from the source;
- `env/uv.lock` only if it equals the `env_lock` every run recorded (review #73), `REPRODUCE.md`, and `BUNDLE.json`
  listing every file with its sha256."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from ...contracts.errors import ApiError, not_found
from ...kernel.config import REPO_ROOT
from ...kernel.db import jload, new_id, row, rows, tx, utcnow
from ...kernel.events import emit
from . import bundle, datasets, plans
from .templates import TemplateRegistry

MAX_FILE = 50_000_000
MAX_TOTAL = 1_000_000_000
RUN_SMALL = 5_000_000  # a run's own JSON / log files above this are treated as possible data and left out
BY_DESIGN = {"inputs": "materialised dataset splits: data, never bundled (rebuild from the source)",
             "home": "the run process's home directory (caches): not a result, never bundled"}


def allowed_run_file(rel: Path, size: int) -> str | None:
    """Allow-list for run-directory files (review #72): records and results, never data. Returns a skip reason."""
    parts, suffix = rel.parts, rel.suffix.lower()
    if len(parts) == 1 and suffix in (".json", ".jsonl", ".log") and size <= RUN_SMALL:
        return None
    if len(parts) == 2 and parts[0] == "predictions" and suffix == ".csv":
        return None
    if len(parts) == 2 and parts[0] in ("figures", "host_figures") and suffix in (".png", ".jpg", ".jpeg"):
        return None
    return "not on the bundle allow-list (records, predictions, figures only): possible data, left out"


def _runs(conn: sqlite3.Connection, plan_id: str) -> list[dict[str, Any]]:
    return rows(conn.execute(
        "SELECT r.id, r.state, r.started_at, r.ended_at, t.state AS task_state, t.error_code, t.error_message, "
        "t.payload FROM runs r JOIN tasks t ON t.id=r.task_id WHERE t.kind='experiment.run' "
        "AND json_extract(t.payload,'$.plan_id')=? ORDER BY r.started_at, r.id", (plan_id,)))


def _dataset_build(conn: sqlite3.Connection, dataset_id: str) -> dict[str, Any] | None:
    t = row(conn.execute("SELECT payload FROM tasks WHERE kind='dataset.build' AND "
                         "json_extract(result,'$.dataset_id')=? ORDER BY created_at LIMIT 1", (dataset_id,)))
    return jload(t["payload"]) if t else None


class _Zip:
    def __init__(self) -> None:
        self.buf = io.BytesIO()
        self.z = zipfile.ZipFile(self.buf, "w", zipfile.ZIP_DEFLATED)
        self.index: dict[str, str] = {}
        self.skipped: list[dict[str, str]] = []
        self.total = 0

    def add(self, name: str, data: bytes) -> None:
        if len(data) > MAX_FILE:
            self.skipped.append({"file": name, "reason": f"larger than {MAX_FILE} bytes"})
            return
        self.total += len(data)
        if self.total > MAX_TOTAL:
            raise ApiError(413, "bundle_too_large", f"bundle exceeds {MAX_TOTAL} bytes")
        self.z.writestr(name, data)
        self.index[name] = hashlib.sha256(data).hexdigest()

    def add_json(self, name: str, obj: Any) -> None:
        self.add(name, json.dumps(obj, indent=1, sort_keys=True, default=str).encode())

    def add_tree(self, prefix: str, root: Path, allow: Any = None) -> None:
        by_design: set[str] = set()
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if not p.is_file() or p.is_symlink() or "__pycache__" in rel.parts:
                continue
            if allow and rel.parts[0] in BY_DESIGN:  # materialised data / process home: one summary line, not per file
                by_design.add(rel.parts[0])
                continue
            why = allow(rel, p.stat().st_size) if allow else None
            if why:
                self.skipped.append({"file": f"{prefix}/{rel.as_posix()}", "reason": why})
            else:
                self.add(f"{prefix}/{rel.as_posix()}", p.read_bytes())
        for d in sorted(by_design):
            self.skipped.append({"file": f"{prefix}/{d}/", "reason": BY_DESIGN[d]})


def _reproduce(plan_id: str, runs: list[dict[str, Any]], tpl: dict[str, Any], ds: dict[str, Any] | None) -> str:
    lines = [f"# Reproduce plan {plan_id}", "",
             "1. Check out the git commit in each `runs/<id>/run.json` (`git.commit`) and install the locked "
             "environment: `uv sync --frozen` with `env/uv.lock` (its sha256 is `env_lock` in run.json).",
             "2. Rebuild the dataset from its source (`dataset/manifest.json`: source, licence, file hashes; build "
             "settings in `dataset/build.json` when it was built by a provider). The rebuilt manifest hash must "
             "equal `dataset.manifest_sha256` in run.json.",
             f"3. Review the template (`template/`, hash {tpl.get('hash')}) and record it as reviewed.",
             "4. Create the plan from `plan/revision-<n>.json`, approve it, and run each run with its `params`, "
             "`seed` and `stage` from run.json. Metrics must match `runs/<id>/metrics.jsonl` (deterministic CPU "
             "templates) or fall within the reported seed spread.", "",
             f"Runs in this bundle: {len(runs)} ({sum(r['state'] != 'succeeded' for r in runs)} not succeeded, "
             "kept on purpose).", f"Dataset: {ds['name'] + ' ' + ds['version'] if ds else 'none'}."]
    return "\n".join(lines) + "\n"


def _add_runs(z: _Zip, data_dir: Path, runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Every run directory (allow-listed) + its task outcome; returns the first run manifest (template / dataset
    used) with the set of recorded env locks under `_locks`."""
    used: dict[str, Any] = {}
    locks: set[str | None] = set()
    for r in runs:
        d = data_dir / "runs" / r["id"]
        if d.is_dir():
            z.add_tree(f"runs/{r['id']}", d, allowed_run_file)
            mf = d / "run.json"
            if mf.is_file():
                manifest = json.loads(mf.read_text(encoding="utf-8"))
                locks.add(manifest.get("env_lock"))
                used = used or manifest
        outcome = {k: r[k] for k in ("state", "task_state", "error_code", "error_message", "started_at", "ended_at")}
        z.add_json(f"runs/{r['id']}/task.json", outcome | {"payload": jload(r["payload"])})
    return {**used, "_locks": locks}


def _add_lock(z: _Zip, locks: set[str | None]) -> None:
    """The environment lock only if it is the one every run recorded (review #73); else skipped with the reason."""
    lock = REPO_ROOT / "uv.lock"
    current = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None
    if current is not None and locks == {current}:
        z.add("env/uv.lock", lock.read_bytes())
    else:
        z.skipped.append({"file": "env/uv.lock", "reason": f"runs recorded env_lock {sorted(map(str, locks))}, the "
                          f"installed uv.lock is {current}: check out each run's git commit for its lock"})


def _add_sources(z: _Zip, conn: sqlite3.Connection, registry: TemplateRegistry,
                 used: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    tpl = used.get("template") or {}
    info = registry.get(str(tpl.get("id"))) if tpl else None
    if info is not None and info.hash == tpl.get("hash"):
        z.add_tree(f"template/{info.manifest.id}", info.path)
    elif tpl:
        z.skipped.append({"file": "template/", "reason": "installed template no longer has the hash the runs used"})
    ds = None
    if used.get("dataset"):
        ds = datasets.get(conn, used["dataset"]["id"])
        z.add_json("dataset/manifest.json", datasets.load_manifest(ds)[1])
        build_task = _dataset_build(conn, ds["id"])
        if build_task:
            z.add_json("dataset/build.json", build_task)
    return tpl, ds


def _publish(conn: sqlite3.Connection, data_dir: Path, plan_id: str, data: bytes, actor: str,
             project_id: str | None) -> dict[str, Any]:
    eid = new_id("exp")
    out = data_dir / "exports" / f"bundle-{plan_id}-{eid}"
    out.mkdir(parents=True)
    path = out / "bundle.zip"
    path.write_bytes(data)
    files = [{"path": path.relative_to(data_dir).as_posix(), "mime": "application/zip",
              "sha256": hashlib.sha256(data).hexdigest()}]
    with tx(conn):
        emit(conn, "export.created", actor=actor, entity_type="export", entity_id=eid, project_id=project_id,
             data={"exporter": "repro_bundle", "doc": f"plan:{plan_id}", "status": "ok", "files": files,
                   "diagnostics": [], "at": utcnow()})
    return {"id": eid, "status": "ok", "files": files}


def build(conn: sqlite3.Connection, data_dir: Path, registry: TemplateRegistry, plan_id: str,
          actor: str) -> dict[str, Any]:
    plan = row(conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)))
    if plan is None:
        raise not_found("plan")
    runs = _runs(conn, plan_id)
    if not runs:
        raise ApiError(409, "no_runs", "this plan has no runs to bundle")
    z = _Zip()
    for r in rows(conn.execute("SELECT revision FROM plan_revisions WHERE plan_id=? ORDER BY revision", (plan_id,))):
        rv = plans.get_revision(conn, plan_id, int(r["revision"]))
        z.add_json(f"plan/revision-{rv['revision']}.json",
                   {k: rv.get(k) for k in ("plan_id", "revision", "content", "content_hash", "change_kind")})
    z.add_json("plan/approvals.json", rows(conn.execute(
        "SELECT subject_type, subject_id, subject_hash, created_at, revoked_at FROM approvals WHERE subject_id LIKE ? "
        "ORDER BY created_at", (f"{plan_id}@%",))))
    used = _add_runs(z, data_dir, runs)
    _add_lock(z, used.pop("_locks"))
    tpl, ds = _add_sources(z, conn, registry, used)
    z.add("REPRODUCE.md", _reproduce(plan_id, runs, tpl, ds).encode())
    states = [{"id": r["id"], "state": r["state"]} for r in runs]
    z.add_json("BUNDLE.json", {"plan_id": plan_id, "title": plan.get("title"), "created_at": utcnow(),
                               "git": bundle._git_commit(), "runs": states, "files": dict(z.index),
                               "skipped": z.skipped,
                               "not_included": "dataset contents and materialised inputs (rebuild from source)"})
    z.z.close()
    out = _publish(conn, data_dir, plan_id, z.buf.getvalue(), actor, plan.get("project_id"))
    return {**out, "runs": len(runs), "failed_runs": sum(r["state"] != "succeeded" for r in runs),
            "skipped": z.skipped}
