"""Task kind `dataset.build`: run a DatasetProvider plugin in a fresh directory under <data>/datasets, verify every
declared file hash, write the manifest and register the dataset (K1)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...contracts.files import FILES_READ, LocalReadContext
from ...contracts.http import HttpContext
from ...contracts.tasks import ResourceClass, TaskContext, TaskKindSpec, TaskOutcome, TaskState
from ...kernel.config import Settings
from ...kernel.db import connect
from .. import plugin_settings
from ..effects.local_read import RootsReader
from ..extensions import Extensions
from ..gateway.keystore import SecretStore
from ..knowledge.http_fetch import HostHttp
from . import datasets

SCHEMA = {
    "type": "object",
    "required": ["provider"],
    "properties": {"provider": {"type": "string"}, "settings": {"type": "object"}},
    "additionalProperties": False,
}


@dataclass
class _BuildCtx:
    out_dir: Path
    http: HttpContext
    task: TaskContext
    local: LocalReadContext | None = None  # only for manifests declaring files:read (core 1.5)

    def cancel_requested(self) -> bool:
        return self.task.cancel_requested()

    def progress(self, stage: str, done: int | None = None, total: int | None = None) -> None:
        self.task.progress(stage, {"done": done, "total": total})


def dataset_kind(settings: Settings, ext: Extensions) -> TaskKindSpec:
    def run(ctx: TaskContext) -> TaskOutcome:
        name = str(ctx.payload["provider"])
        prov = ext.dataset_providers.get(name)
        if prov is None:
            return TaskOutcome(TaskState.FAILED, error_code="unknown_provider", error_message=name)
        conn = connect(settings.db_path)
        try:
            eff = plugin_settings.effective(conn, SecretStore(settings.config_dir), ext, "datasources", name,
                                            ctx.payload.get("settings"))
        finally:
            conn.close()
        out = settings.datasets_dir / f"{name}-{ctx.run_id}"
        out.mkdir(parents=True)
        http = HostHttp(ext.granted_hosts(f"datasources:{name}", prov.spec.hosts))
        local = RootsReader(settings.db_path) if ext.has_capability(f"datasources:{name}", FILES_READ) else None
        d = prov.build(_BuildCtx(out, http, ctx, local), eff)
        for f in d.files:
            p = (out / f.path).resolve()
            if not p.is_relative_to(out.resolve()) or hashlib.sha256(p.read_bytes()).hexdigest() != f.sha256:
                return TaskOutcome(TaskState.FAILED, error_code="dataset_hash_mismatch", error_message=f.path)
        manifest: dict[str, Any] = {
            "name": d.name,
            "version": d.version,
            "source": d.source,
            "license": d.license,
            "group_key": d.group_key,
            "overlapping_windows": d.overlapping_windows,
            "table": d.table,
            "files": [{"path": f.path, "sha256": f.sha256} for f in d.files],
            "expected_source_sha256": d.expected_source_sha256,
            "observed_source_sha256": d.observed_source_sha256,
            "egress": d.egress,
            **dict(d.extra),
            "built_by": f"plugin:{name}@{prov.spec.version}",
        }
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        conn = connect(settings.db_path)
        try:
            ds = datasets.register(conn, out, f"task:{ctx.task_id}")
        finally:
            conn.close()
        unverified = d.expected_source_sha256 is None
        return TaskOutcome(
            TaskState.SUCCEEDED,
            {"dataset_id": ds["id"], "path": str(out), "upstream_checksum": "unverified" if unverified else "matched"},
        )

    return TaskKindSpec(
        "dataset.build",
        "build a dataset with a DatasetProvider plugin",
        ResourceClass.NETWORK,
        payload_schema=SCHEMA,
        handler=run,
    )
