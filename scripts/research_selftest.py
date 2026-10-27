"""Run the fixed research self-test (scripts/research_selftest.json, ADR-188) and print a pass/fail report.

Usage: .venv/bin/python scripts/research_selftest.py [--out report.json]
Core items run from the repo root; plugin items from their plugin directory (as scripts/check.sh does). Exit 1 if
any item fails or no longer exists. Mock provider only: the tests never call a paid model or the network."""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "research_selftest.json"


def items() -> list[dict[str, str]]:
    return list(json.loads(MANIFEST.read_text(encoding="utf-8"))["items"])


def location(test: str) -> tuple[Path, str]:
    """(directory to run pytest in, node id relative to it)."""
    if test.startswith("plugins/"):
        plugin = "/".join(test.split("/")[:2])
        return ROOT / plugin, test[len(plugin) + 1 :]
    return ROOT, test


def _outcomes(cwd: Path, nodes: list[str]) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as td:
        xml = Path(td) / "r.xml"
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", "-o", "addopts=",
                        f"--junitxml={xml}", *nodes], cwd=cwd, check=False, capture_output=True)
        if not xml.is_file():
            return {}
        out = {}
        for case in ET.parse(xml).getroot().iter("testcase"):  # noqa: S314 - written by our own pytest run
            file = case.get("classname", "").replace(".", "/")
            name = f"{file}.py::{case.get('name')}"
            bad = case.find("failure") is not None or case.find("error") is not None
            out[name] = "skipped" if case.find("skipped") is not None else "failed" if bad else "passed"
        return out


def run() -> dict[str, object]:
    groups: dict[Path, list[tuple[dict[str, str], str]]] = {}
    for it in items():
        cwd, node = location(it["test"])
        groups.setdefault(cwd, []).append((it, node))
    results = []
    for cwd, entries in groups.items():
        got = _outcomes(cwd, [n for _, n in entries])
        for it, node in entries:
            results.append({**it, "outcome": got.get(node, "missing")})
    results.sort(key=lambda r: r["id"])
    passed = sum(r["outcome"] == "passed" for r in results)
    return {"run_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"), "passed": passed,
            "total": len(results), "items": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path)
    report = run()
    a = ap.parse_args()
    for r in report["items"]:  # type: ignore[attr-defined]
        print(f"{r['id']}  {r['outcome']:7}  {r['area']}: {r['proves']}")
    print(f"{report['passed']}/{report['total']} passed")
    if a.out:
        a.out.write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
