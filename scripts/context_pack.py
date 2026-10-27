"""Emit a bounded reading pack for ONE module before editing it (HARDENING #13): its files, public names,
who imports it, what it imports, and the ARCHITECTURE contract section it belongs to.

Usage: python scripts/context_pack.py researchos.services.orchestration [--max-lines 600]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import grimp

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_OF = {"kernel": "K1", "orchestration": "K2", "approval": "K3", "effects": "K4", "execution": "K5",
               "platform": "K5/§9", "gateway": "K6", "knowledge": "K7", "content": "K8", "verification": "K9"}


def public_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef)
            and not n.name.startswith("_")]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    mod = sys.argv[1]
    budget = int(sys.argv[sys.argv.index("--max-lines") + 1]) if "--max-lines" in sys.argv else 600
    graph = grimp.build_graph("researchos", "researchos_worker")
    base = ROOT / "services/api" / Path(*mod.split("."))
    files = sorted(base.rglob("*.py")) if base.is_dir() else [base.with_suffix(".py")]
    print(f"# context pack: {mod}")
    print(f"contract: {next((v for k, v in CONTRACT_OF.items() if k in mod), 'see ARCHITECTURE.md')}")
    mods = {mod} | (graph.find_descendants(mod) if mod in graph.modules else set())

    def inside(m: str) -> bool:
        return m == mod or m.startswith(mod + ".")

    importers = {i for m in mods for i in graph.find_modules_that_directly_import(m) if not inside(i)}
    imported = {i for m in mods for i in graph.find_modules_directly_imported_by(m) if not inside(i)}
    print("imported by:", sorted(importers))
    print("imports:", sorted(i for i in imported if i.startswith("researchos")))
    used = 0
    for f in files:
        lines = f.read_text(encoding="utf-8").splitlines()
        print(f"\n## {f.relative_to(ROOT)} ({len(lines)} lines) public: {public_names(f)}")
        take = lines[: max(0, budget - used)]
        print("\n".join(take))
        used += len(take)
        if used >= budget:
            print(f"\n[truncated at {budget} lines]")
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
