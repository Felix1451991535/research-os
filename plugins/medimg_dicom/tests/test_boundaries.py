"""Plugin rules: only researchos.contracts / researchos.sdk imported from core, honest manifest, entry points load."""

from __future__ import annotations

import ast
import json
from importlib.metadata import distribution
from pathlib import Path

from researchos.contracts.datasources import DatasetProvider

import researchos_plugin_medimg_dicom as pkg

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ("researchos.contracts", "researchos.sdk")


def _imports(path: Path) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.append(node.module)
    return mods


def test_only_public_core_modules_are_imported() -> None:
    for folder in ("src", "tests"):
        for path in (ROOT / folder).rglob("*.py"):
            for mod in _imports(path):
                if mod == "researchos" or mod.startswith("researchos."):
                    assert mod.startswith(tuple(a + "." for a in ALLOWED)) or mod in ALLOWED, f"{path}: {mod}"


def test_manifest_declares_only_local_read() -> None:
    m = pkg.MANIFEST
    assert m.capabilities == ["files:read"] and m.core_contract == ">=1.7,<2" and m.migration_namespace is None
    json.dumps(m.settings_schema)


def test_entry_points_load() -> None:
    eps = list(distribution("researchos-plugin-medimg_dicom").entry_points)
    groups: dict[str, list[str]] = {}
    for ep in eps:
        groups.setdefault(ep.group, []).append(ep.name)
    assert groups == {
        "researchos.plugins": ["researchos_plugin_medimg_dicom"],
        "researchos.datasources": ["medimg_dicom_folder"],
    }
    provider: DatasetProvider = next(ep for ep in eps if ep.group == "researchos.datasources").load()()
    assert provider.spec.kind == "dataset" and provider.spec.hosts == ()


def test_file_size_limits() -> None:
    skip = {".venv", "dist", ".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__"}
    for path in ROOT.rglob("*"):
        if path.is_file() and not skip & set(path.parts):
            assert path.stat().st_size <= 5_000_000, path
            if path.suffix in (".py", ".md", ".json", ".toml", ".txt"):
                assert len(path.read_text(encoding="utf-8").splitlines()) <= 400, path
