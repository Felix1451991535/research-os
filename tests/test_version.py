"""Version single source of truth (ADR-222): pyproject.toml; every derived copy must agree."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import researchos

ROOT = Path(__file__).resolve().parents[1]


def _source() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_runtime_version_is_read_from_pyproject() -> None:
    assert researchos.__version__ == _source()


def test_derived_copies_match_the_source() -> None:
    v = _source()
    assert json.loads((ROOT / "apps/web/package.json").read_text(encoding="utf-8"))["version"] == v
    assert json.loads((ROOT / "packages/contracts/openapi.json").read_text(encoding="utf-8"))["info"]["version"] == v
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert re.search(r'^name = "researchos"\nversion = "([^"]+)"$', lock, re.M).group(1) == v  # type: ignore[union-attr]


def test_no_other_hard_coded_version_in_the_backend() -> None:
    init = (ROOT / "services/api/researchos/__init__.py").read_text(encoding="utf-8")
    assert not re.search(r'__version__\s*=\s*"\d', init)
