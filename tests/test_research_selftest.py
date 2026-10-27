"""Guard for the fixed research self-test (ADR-188): every listed test still exists, ids are unique and numbered.
A renamed or deleted test must not silently drop out of the regression set."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("research_selftest", ROOT / "scripts" / "research_selftest.py")
assert spec and spec.loader
selftest = importlib.util.module_from_spec(spec)
sys.modules["research_selftest"] = selftest
spec.loader.exec_module(selftest)


def _defined(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for c in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        out |= {f"{c.name}::{m.name}" for m in c.body if isinstance(m, ast.FunctionDef)}
    return out


def test_manifest_items_exist_and_are_numbered() -> None:
    items = selftest.items()
    assert 18 <= len(items) <= 25
    assert [i["id"] for i in items] == [f"ST-{n:02d}" for n in range(1, len(items) + 1)]
    for it in items:
        assert it["area"] and it["proves"], it["id"]
        cwd, node = selftest.location(it["test"])
        file, _, name = node.partition("::")
        assert (cwd / file).is_file(), it["id"]
        assert name in _defined(cwd / file), f"{it['id']}: {it['test']} no longer exists"


def test_runner_maps_outcomes_to_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """A node the run did not report (e.g. deleted after the guard ran) counts as missing, never as passed."""
    monkeypatch.setattr(selftest, "_outcomes", lambda cwd, nodes: {n: "passed" for n in nodes[:-1]})
    report = selftest.run()
    outcomes = [i["outcome"] for i in report["items"]]
    assert outcomes.count("missing") >= 1 and report["passed"] == report["total"] - outcomes.count("missing")
