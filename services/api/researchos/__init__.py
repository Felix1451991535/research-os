"""Research OS. The version has ONE source of truth: `[project] version` in pyproject.toml (ADR-222). The source tree
(dev checkout and the Windows install, which ships pyproject.toml in app\\) is read directly; an installed wheel
falls back to its metadata. apps/web/package.json, packages/contracts/openapi.json and uv.lock are derived copies,
rewritten by scripts/release/bump_version.py and checked by tests/test_version.py."""
from __future__ import annotations

import tomllib
from pathlib import Path


def _version() -> str:
    try:
        data = tomllib.loads((Path(__file__).resolve().parents[3] / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        from importlib.metadata import version
        return version("researchos")


__version__ = _version()
