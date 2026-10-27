"""Architecture health gate (ARCHITECTURE §3, HARDENING #8/#13).

Exit 1 on NEW violations: file > 400 lines, import cycle between sibling packages, OpenAPI snapshot drift.
Existing violations recorded in scripts/arch_baseline.json are reported but tolerated (ratchet: the baseline may
only shrink). Flags: --changed (line cap on git-changed files only; fast pre-commit), --no-openapi,
--write-baseline. Always prints a report (largest files, TODO count) for milestone reviews."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_LINES = 400
CODE_DIRS = [
    "services/api/researchos", "workers/researchos_worker", "apps/web/src", "tests", "scripts", "templates",
    "plugins", "examples",
]
EXCLUDE = re.compile(r"(schema\.d\.ts$|/node_modules/|/dist/|__pycache__)")
EXTS = {".py", ".ts", ".tsx", ".ps1", ".sh"}


def code_files() -> list[Path]:
    out = []
    for d in CODE_DIRS:
        base = ROOT / d
        if base.is_dir():
            out += [p for p in base.rglob("*") if p.suffix in EXTS and p.is_file() and not EXCLUDE.search(p.as_posix())]
    return out


def line_report(files: list[Path]) -> tuple[list[tuple[int, str]], int]:
    def text(p: Path) -> str:
        return p.read_text(encoding="utf-8", errors="replace")
    sizes = sorted(((len(text(p).splitlines()), p.relative_to(ROOT).as_posix()) for p in files), reverse=True)
    # this file contains the marker pattern itself; do not count it
    todos = sum(len(re.findall(r"\b(TODO|FIXME|XXX)\b", text(p))) for p in files if p.name != "arch_health.py")
    return sizes, todos


def package_cycles() -> list[str]:
    """Cycles between sibling packages (e.g. services.approval <-> services.orchestration)."""
    import grimp
    graph = grimp.build_graph("researchos", "researchos_worker")
    problems = []
    for parent in ("researchos.services", "researchos.api", "researchos.kernel", "researchos.platform"):
        children = sorted(graph.find_children(parent))
        for i, a in enumerate(children):
            for b in children[i + 1:]:
                ab = graph.find_shortest_chain(importer=a, imported=b, as_packages=True)
                ba = graph.find_shortest_chain(importer=b, imported=a, as_packages=True)
                if ab and ba:
                    problems.append(f"cycle {a} <-> {b}: {' -> '.join(ab)} / {' -> '.join(ba)}")
    return problems


def openapi_drift() -> str | None:
    snap = ROOT / "packages/contracts/openapi.json"
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "RESEARCHOS_DATA_DIR": d}
        out = subprocess.run([sys.executable, "-m", "researchos.app.cli", "openapi"], capture_output=True, text=True,
                             env=env, cwd=ROOT, check=True).stdout
    live = json.loads(out)
    if not snap.is_file() or json.loads(snap.read_text(encoding="utf-8")) != live:
        return "OpenAPI changed: run scripts/gen-contracts.sh, review the diff, commit openapi.json + schema.d.ts"
    return None


BASELINE = ROOT / "scripts" / "arch_baseline.json"


def changed_files() -> set[str]:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True,
                         cwd=ROOT, check=True).stdout
    return {line[3:].strip() for line in out.splitlines() if line.strip()}


def load_baseline() -> dict[str, list[str]]:
    if BASELINE.is_file():
        data: dict[str, list[str]] = json.loads(BASELINE.read_text(encoding="utf-8"))
        return data
    return {"oversize": [], "cycles": []}


def main() -> int:
    files = code_files()
    if "--changed" in sys.argv:
        ch = changed_files()
        files = [f for f in files if f.relative_to(ROOT).as_posix() in ch]
    sizes, todos = line_report(files)
    base = load_baseline()
    over_all = [(n, f) for n, f in sizes if n > MAX_LINES]
    cycles_all = package_cycles()
    if "--write-baseline" in sys.argv:
        BASELINE.write_text(json.dumps({"oversize": sorted(f for _, f in over_all), "cycles": sorted(cycles_all)},
                                       indent=1) + "\n", encoding="utf-8")
    over = [(n, f) for n, f in over_all if f not in base["oversize"]]
    cycles = [c for c in cycles_all if c not in base["cycles"]]
    for n, f in over_all:
        if f in base["oversize"]:
            print(f"baseline (tolerated, must shrink): {f} ({n})")
    drift = openapi_drift() if "--no-openapi" not in sys.argv and "--changed" not in sys.argv else None
    print("== arch health ==")
    print(f"files: {len(files)}  total lines: {sum(n for n, _ in sizes)}  TODO/FIXME: {todos}")
    print("largest files:")
    for n, f in sizes[:8]:
        print(f"  {n:5d}  {f}")
    for n, f in over:
        print(f"FAIL >{MAX_LINES} lines: {f} ({n})")
    for c in cycles:
        print(f"FAIL {c}")
    if drift:
        print(f"FAIL {drift}")
    ok = not over and not cycles and not drift
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
