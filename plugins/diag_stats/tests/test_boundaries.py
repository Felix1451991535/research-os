"""Plugin rules: only researchos.contracts / researchos.sdk imported from core, one manifest, honest (empty)
capabilities, no network / file-writing modules in the tools, entry points load, file limits."""

from __future__ import annotations

import ast
from importlib.metadata import distribution
from pathlib import Path

from researchos.contracts.gateway import ToolBinding
from researchos.contracts.plugins import PluginManifest

import researchos_plugin_diag_stats as pkg

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "researchos_plugin_diag_stats"
ALLOWED = ("researchos.contracts", "researchos.sdk")
TOOLS = [
    "stats_cluster_bootstrap",
    "stats_compare_groups",
    "stats_diag_metrics",
    "stats_multiplicity",
    "stats_paired_tests",
    "stats_power",
    "stats_roc_auc",
    "stats_survival",
]


def _imports(path: Path) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.append(node.module)
    return mods


def test_only_public_core_modules_are_imported() -> None:
    for folder in ("src", "tests", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            for mod in _imports(path):
                if mod == "researchos" or mod.startswith("researchos."):
                    assert mod in ALLOWED or mod.startswith(tuple(a + "." for a in ALLOWED)), f"{path}: {mod}"


def test_tools_have_no_network_or_file_io() -> None:
    banned = {"httpx", "requests", "urllib", "urllib.request", "socket", "http.client", "shutil", "subprocess"}
    for path in SRC.glob("*.py"):
        assert not banned & set(_imports(path)), path
        if path.name not in ("report.py", "render.py"):
            text = path.read_text(encoding="utf-8")
            for call in ("open(", "write_text", "write_bytes", "mkdir", "np.save"):
                assert call not in text, f"{path.name} uses {call}"


def test_manifest() -> None:
    m = pkg.MANIFEST
    assert isinstance(m, PluginManifest) and m.version == "0.1.0" and m.core_contract == ">=1.8,<2"
    assert m.capabilities == [] and m.settings_schema == {}


def test_entry_points_are_registered_and_load() -> None:
    groups: dict[str, list[str]] = {}
    eps = list(distribution("researchos-plugin-diag_stats").entry_points)
    for ep in eps:
        groups.setdefault(ep.group, []).append(ep.name)
    assert groups["researchos.plugins"] == ["researchos_plugin_diag_stats"]
    assert sorted(groups["researchos.tools"]) == TOOLS and groups["researchos.exporters"] == ["stats_report"]
    for ep in eps:
        obj = ep.load()
        if ep.group == "researchos.tools":
            assert isinstance(obj, ToolBinding) and obj.spec.name == ep.name
            s = obj.spec
            assert s.read_only and not s.destructive and s.side_effect_scope == "none" and s.needs == ()
        elif ep.group == "researchos.exporters":
            assert obj().spec.name == "stats_report"


def test_file_size_limits() -> None:
    for path in ROOT.rglob("*"):
        if path.is_file() and ".venv" not in path.parts and "dist" not in path.parts:
            assert path.stat().st_size <= 5_000_000, path
            if path.suffix in (".py", ".md", ".json", ".toml", ".txt"):
                assert len(path.read_text(encoding="utf-8").splitlines()) <= 400, path


def test_invalid_input_is_a_tool_error() -> None:
    from researchos.contracts.gateway import ToolError

    from researchos_plugin_diag_stats._common import StatsInputError

    e = StatsInputError("p_values must lie in [0, 1]")
    assert isinstance(e, ToolError) and isinstance(e, ValueError) and e.code == "invalid_input"
    assert str(e) == "p_values must lie in [0, 1]"
