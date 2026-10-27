"""Plugin rules: only researchos.contracts / researchos.sdk imported from core, one manifest, honest capabilities,
no loopback/local Zotero API, entry points load, file limits."""

from __future__ import annotations

import ast
import json
from importlib.metadata import distribution
from pathlib import Path

from researchos.contracts.plugins import PluginManifest

import researchos_plugin_zotero_import as pkg

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
                    assert mod in ALLOWED or mod.startswith(tuple(a + "." for a in ALLOWED)), f"{path}: {mod}"


def test_no_network_libraries_or_loopback_in_source() -> None:
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not {"httpx", "requests", "urllib.request", "socket", "http.client"} & set(_imports(path)), path
        for bad in ("localhost", "127.0.0.1", "23119", "[::1]"):  # 23119 = Zotero desktop local API port
            assert bad not in text, f"{path} mentions {bad}"


def test_manifest() -> None:
    m = pkg.MANIFEST
    assert isinstance(m, PluginManifest) and m.version == "0.1.0" and m.core_contract == ">=1.3,<2"
    assert m.capabilities == ["network:api.zotero.org"] and m.migration_namespace is None
    secret = m.settings_schema["properties"]["zotero_web"]["properties"]["api_key"]  # type: ignore[index]
    assert secret["writeOnly"] is True
    json.dumps(m.settings_schema)


def test_entry_points_are_registered_and_load() -> None:
    eps = list(distribution("researchos-plugin-zotero_import").entry_points)
    groups: dict[str, list[str]] = {}
    for ep in eps:
        groups.setdefault(ep.group, []).append(ep.name)
    assert groups["researchos.plugins"] == ["researchos_plugin_zotero_import"]
    assert sorted(groups["researchos.importers"]) == ["zotero_csljson", "zotero_rdf", "zotero_web"]
    for ep in eps:
        if ep.group == "researchos.importers":
            obj = ep.load()()
            assert obj.spec.name == ep.name and obj.spec.kind == "bibliographic"
            assert hasattr(obj, "import_records") != hasattr(obj, "parse_file")
            assert obj.spec.hosts == (("api.zotero.org",) if ep.name == "zotero_web" else ())


def test_file_size_limits() -> None:
    for path in ROOT.rglob("*"):
        if path.is_file() and ".venv" not in path.parts and "dist" not in path.parts:
            assert path.stat().st_size <= 5_000_000, path
            if path.suffix in (".py", ".md", ".json", ".toml", ".txt", ".rdf"):
                assert len(path.read_text(encoding="utf-8").splitlines()) <= 400, path
