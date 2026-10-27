"""RunBundle preparation: run dir, params, materialized splits (stage-limited), worker-written manifest."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from ...contracts.execution import STAGE_SPLITS, RunBundle
from ...kernel.config import REPO_ROOT, Settings
from ...kernel.db import Row
from ...platform.env import child_env
from . import datasets
from .templates import TemplateInfo


def _git_commit() -> dict[str, Any]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": sha or None, "dirty": dirty}


def _lock_hash() -> str | None:
    p = REPO_ROOT / "uv.lock"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def device_label(resource_class: str) -> str:
    """Honest label: GPU runs are labelled by what torch actually reports inside the run; the bundle states the
    requested class and the host CPU."""
    return f"{resource_class}:{platform.processor() or platform.machine()}"


def prepare(
    settings: Settings,
    run_id: str,
    tpl: TemplateInfo,
    revision: Row,
    stage: str,
    params: dict[str, Any],
    seed: int,
    dataset: Row | None,
    catalog_revision: int,
    control: str | None = None,
) -> tuple[RunBundle, dict[str, Any]]:
    content = revision["content"]
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "params.json").write_text(json.dumps(params, sort_keys=True), encoding="utf-8")
    mat: dict[str, Any] = {"ids": {}, "groups": {}, "split_hash": None, "overlapping_windows": False}
    allowed = STAGE_SPLITS[stage]
    if dataset is not None and allowed:
        shuffle = seed if control == "shuffled_labels" else None
        mat = datasets.materialize(dataset, content.get("split", {}), allowed, run_dir / "inputs", shuffle)
        mat["groups"] = _groups_of(run_dir / "inputs", allowed)
    device = device_label(tpl.manifest.resource_class)
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "stage": stage,
        "seed": seed,
        "params": params,
        "plan": {"id": revision["plan_id"], "revision": revision["revision"], "hash": revision["content_hash"]},
        "template": {"id": tpl.manifest.id, "version": tpl.manifest.version, "hash": tpl.hash},
        "dataset": {"id": dataset["id"], "manifest_sha256": dataset["manifest_sha256"]} if dataset else None,
        "split_hash": mat.get("split_hash"),
        "splits": list(allowed),
        "git": _git_commit(),
        "env_lock": _lock_hash(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "catalog_revision": catalog_revision,
        "device": device,
        "control": control,
    }
    (run_dir / "run.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    limits = dict(content.get("limits", {}))
    extra = {"ROS_RUN_DIR": str(run_dir), "PYTHONHASHSEED": str(seed), "OMP_NUM_THREADS": str(limits.get("threads", 4))}
    if control:
        extra["ROS_CONTROL"] = control  # negative control: train labels were shuffled by the worker, not the template
    env = child_env(run_dir / "home", extra)
    return RunBundle(run_id, run_dir, tpl.entry, env, limits, device), mat


def _groups_of(inputs: Path, splits: tuple[str, ...]) -> dict[str, list[str]]:
    import numpy as np

    out: dict[str, list[str]] = {}
    for s in splits:
        if (inputs / f"{s}.npz").is_file():
            with np.load(inputs / f"{s}.npz", allow_pickle=False) as npz:
                out[s] = sorted({str(g) for g in npz["group"]})
    return out
