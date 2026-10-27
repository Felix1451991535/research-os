"""Bump the version (ADR-222/225): `[project] version` in pyproject.toml is the source; the derived copies
apps/web/package.json, packages/contracts/openapi.json (info.version) and the researchos entry of uv.lock follow.
Each rewrite must hit exactly one place. uv.lock is edited as text (the editable project entry is the only place the
version appears; `uv lock --offline` cannot re-resolve without every wheel cached); `uv lock --check` confirms it.

Usage: python scripts/release/bump_version.py <new-version>     (then: pytest tests/test_version.py)"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

from packaging.version import InvalidVersion, Version

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT  # noqa: E402

# file -> pattern whose group 1 / group 2 surround the version (each must match exactly once)
REWRITES: dict[str, re.Pattern[str]] = {
    "pyproject.toml": re.compile(r'^(version = ")[^"]+(")', re.M),
    "apps/web/package.json": re.compile(r'^(  "version": ")[^"]+(")', re.M),
    "packages/contracts/openapi.json": re.compile(r'("info": \{[^}]*?"version": ")[^"]+(")'),
    "uv.lock": re.compile(r'^(name = "researchos"\nversion = ")[^"]+(")', re.M),
}


def current(root: Path = ROOT) -> str:
    return str(tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])


def rewrite(root: Path, new: str) -> list[str]:
    """Rewrite pyproject.toml and its derived copies; returns the changed files."""
    texts = {name: (root / name).read_text(encoding="utf-8") for name in REWRITES}
    for name, pattern in REWRITES.items():
        texts[name], n = pattern.subn(rf"\g<1>{new}\g<2>", texts[name], count=2)
        if n != 1:
            raise ValueError(f"{name}: expected one version field, found {n}")
    for name, text in texts.items():  # written only after every pattern matched
        (root / name).write_text(text, encoding="utf-8")
    return list(REWRITES)


def check_new(old: str, new: str) -> None:
    try:
        if Version(new) <= Version(old):
            raise ValueError(f"{new} is not newer than {old}")
    except InvalidVersion as e:
        raise ValueError(f"not a PEP 440 version: {new}") from e


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    old, new = current(), args[0]
    try:
        check_new(old, new)
    except ValueError as e:
        print(f"bump_version: {e}", file=sys.stderr)
        return 2
    print(f"{old} -> {new}: {', '.join(rewrite(ROOT, new))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
