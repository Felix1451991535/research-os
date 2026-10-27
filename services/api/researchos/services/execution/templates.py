"""Template registry: templates/<id>/template.json + code, hashed; only hashes listed in REVIEWED.json may run.
A changed file changes the hash -> status 'changed' -> runs are blocked until re-reviewed (fault case
"template hash change")."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ...contracts.execution import TemplateManifest

REVIEWED_FILE = "REVIEWED.json"


def template_hash(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            h.update(p.relative_to(d).as_posix().encode())
            h.update(b"\0")
            h.update(p.read_bytes())
    return h.hexdigest()


@dataclass(frozen=True)
class TemplateInfo:
    manifest: TemplateManifest
    path: Path
    hash: str
    status: str  # reviewed | changed | unreviewed

    @property
    def entry(self) -> Path:
        return self.path / self.manifest.entry


def _load_manifest(d: Path) -> TemplateManifest:
    raw = json.loads((d / "template.json").read_text(encoding="utf-8"))
    return TemplateManifest(
        id=raw["id"],
        version=str(raw["version"]),
        entry=raw["entry"],
        description=raw.get("description", ""),
        params_schema=raw.get("params_schema", {}),
        resource_class=raw.get("resource_class", "cpu"),
        metrics=tuple(raw.get("metrics", [])),
        task=raw.get("task", {}),
        requires=tuple(raw.get("requires", [])),
    )


class TemplateRegistry:
    """`dirs` are trusted roots (core templates, owner-configured dirs) whose own REVIEWED.json counts.
    `plugin_templates` are template directories shipped by plugins: their hashes count as reviewed ONLY when listed
    in the owner's file `owner_reviewed` (<data>/config/REVIEWED.json) — a plugin cannot approve itself."""

    def __init__(self, dirs: list[Path], plugin_templates: list[Path] | None = None,
                 owner_reviewed: Path | None = None) -> None:
        self.dirs = [d for d in dirs if d.is_dir()]
        self.plugin_templates = [p for p in plugin_templates or [] if (p / "template.json").is_file()]
        self.owner_reviewed = owner_reviewed

    @staticmethod
    def _read(f: Path | None) -> dict[str, str]:
        return json.loads(f.read_text(encoding="utf-8")) if f is not None and f.is_file() else {}

    def _reviewed(self, root: Path) -> dict[str, str]:
        return {**self._read(root / REVIEWED_FILE), **self._read(self.owner_reviewed)}

    def _info(self, d: Path, reviewed: dict[str, str]) -> TemplateInfo:
        m = _load_manifest(d)
        h = template_hash(d)
        status = "unreviewed" if m.id not in reviewed else ("reviewed" if reviewed[m.id] == h else "changed")
        return TemplateInfo(m, d, h, status)

    def all(self) -> dict[str, TemplateInfo]:
        out: dict[str, TemplateInfo] = {}
        owner = self._read(self.owner_reviewed)
        for d in self.plugin_templates:
            info = self._info(d, owner)
            out[info.manifest.id] = info
        for root in self.dirs:
            reviewed = self._reviewed(root)
            for d in sorted(p for p in root.iterdir() if (p / "template.json").is_file()):
                info = self._info(d, reviewed)
                out[info.manifest.id] = info  # trusted roots win over plugin templates with the same id
        return out

    def get(self, template_id: str) -> TemplateInfo | None:
        return self.all().get(template_id)
