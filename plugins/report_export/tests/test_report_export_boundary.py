"""Architecture and installed entry-point checks."""

import ast
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]


def test_public_core_imports_only() -> None:
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name == "researchos" or name.startswith("researchos."):
                    assert name in {"researchos.contracts", "researchos.sdk"} or name.startswith(
                        ("researchos.contracts.", "researchos.sdk.")
                    ), (path, name)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "__import__", path


def test_source_size() -> None:
    for path in ROOT.rglob("*.py"):
        if ".venv" not in path.parts:
            assert len(path.read_text().splitlines()) <= 400, path


def test_entry_points_load() -> None:
    dist = distribution("researchos-plugin-report_export")
    entries = [e for e in dist.entry_points if e.group.startswith("researchos.")]
    assert len([e for e in entries if e.group == "researchos.plugins"]) == 1
    for entry in entries:
        obj: Any = entry.load()
        if entry.group == "researchos.plugins":
            assert obj.core_contract == ">=1.2,<2" and obj.version == "0.1.0"
        else:
            value = obj() if callable(obj) else obj
            assert value is not None


def test_slides_entry_point_declared() -> None:
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["project"]["entry-points"]["researchos.exporters"]["report_slides"] == (
        "researchos_plugin_report_export.slides:SlidesExporter"
    )
