"""Plugin rules: only researchos.contracts / researchos.sdk imported from core, one manifest, honest capabilities,
registered objects match the specs, size/complexity-friendly files."""

from __future__ import annotations

import ast
import json
from importlib.metadata import distribution
from pathlib import Path

from researchos.contracts.datasources import DatasetProvider
from researchos.contracts.execution import TemplatePackage
from researchos.contracts.plugins import PluginManifest

import researchos_plugin_physio_signals as pkg

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


def test_manifest() -> None:
    m = pkg.MANIFEST
    assert isinstance(m, PluginManifest) and m.version == "0.1.0" and m.core_contract == ">=1.7,<2"
    assert m.capabilities == ["network:physionet.org", "files:read"] and m.migration_namespace is None
    json.dumps(m.settings_schema)


def test_entry_points_are_registered_and_load() -> None:
    eps = list(distribution("researchos-plugin-physio_signals").entry_points)
    groups: dict[str, list[str]] = {}
    for ep in eps:
        groups.setdefault(ep.group, []).append(ep.name)
    assert groups["researchos.plugins"] == ["researchos_plugin_physio_signals"]
    assert sorted(groups["researchos.datasources"]) == [
        "physio_chbmit",
        "physio_eegmmi",
        "physio_mitbih",
        "physio_ptbxl",
        "physio_synth_eeg",
    ]
    assert groups["researchos.templates"] == ["physio_ecg_baseline"]
    for ep in eps:
        obj = ep.load()
        if ep.group == "researchos.datasources":
            inst = obj()
            provider: DatasetProvider = inst
            assert callable(provider.build) and provider.spec.kind == "dataset"
            hosts = () if ep.name == "physio_synth_eeg" else ("physionet.org",)  # synthetic: no network at all
            assert inst.spec.name == ep.name and inst.spec.hosts == hosts
        elif ep.group == "researchos.templates":
            tp = obj()
            assert isinstance(tp, TemplatePackage) and (tp.root / "template.json").is_file()
            assert json.loads((tp.root / "template.json").read_text())["id"] == ep.name


def test_file_size_limits() -> None:
    for path in ROOT.rglob("*"):
        tool_output = {".venv", "dist", ".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__"}
        if path.is_file() and not tool_output & set(path.parts):
            assert path.stat().st_size <= 5_000_000, path
            if path.suffix in (".py", ".md", ".json", ".toml", ".txt"):
                assert len(path.read_text(encoding="utf-8").splitlines()) <= 400, path
