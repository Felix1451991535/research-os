"""Static checks shared by TemplateConformance and the host's template gate (stdlib only).

A template must be self-contained: the reviewed hash covers only the template directory, so its code may import
only the standard library, `researchos.sdk`, the third-party packages it declares in `requires`, and modules that
live inside the template directory itself. Importing the plugin's own package (or anything else) would execute
code the owner never reviewed under that hash.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable
from pathlib import Path


def _imports(path: Path) -> Iterable[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((a.name, node.lineno) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: only valid inside a package, which a template dir is not
                yield ("." * node.level + (node.module or ""), node.lineno)
            elif node.module:
                yield (node.module, node.lineno)


def template_import_violations(root: Path, requires: Iterable[str]) -> list[str]:
    """Returns human-readable violations (empty = self-contained)."""
    py_files = sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    local = {p.stem for p in py_files} | {p.name for p in root.iterdir() if p.is_dir()}
    allowed = set(sys.stdlib_module_names) | set(requires) | local
    out = []
    for p in py_files:
        try:
            found = list(_imports(p))
        except SyntaxError as e:
            out.append(f"{p.relative_to(root).as_posix()}: syntax error: {e.msg}")
            continue
        for mod, line in found:
            top = mod.split(".")[0]
            ok = mod == "researchos.sdk" or mod.startswith("researchos.sdk.") if top == "researchos" else top in allowed
            if mod.startswith(".") or not ok:
                out.append(
                    f"{p.relative_to(root).as_posix()}:{line}: imports '{mod}' (not stdlib, researchos.sdk, "
                    "a declared requirement or a template-local module)"
                )
    return out
