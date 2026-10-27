"""Shared test helpers: frozen golden values and tolerance checks."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from researchos.contracts.gateway import ToolBinding
from researchos.sdk.testing import FakeToolContext

ROOT = Path(__file__).resolve().parents[1]
GOLDEN: dict[str, Any] = json.loads((ROOT / "fixtures" / "golden.json").read_text(encoding="utf-8"))
REL = 1e-9
ABS = 1e-12  # only matters for reference values that are exactly 0


def close(actual: object, expected: object, where: str = "") -> None:
    """Recursive comparison: numbers within relative error 1e-9, None/bool/str/structure exact."""
    if expected is None or isinstance(expected, bool | str):
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"
    elif isinstance(expected, int | float):
        assert isinstance(actual, int | float) and not isinstance(actual, bool), f"{where}: {actual!r}"
        assert math.isclose(actual, expected, rel_tol=REL, abs_tol=ABS), f"{where}: {actual!r} vs {expected!r}"
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{where}: {actual!r}"
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            close(a, e, f"{where}[{i}]")
    else:
        raise TypeError(f"unsupported golden type at {where}: {type(expected)}")


def call(binding: ToolBinding, args: dict[str, Any]) -> dict[str, Any]:
    out = binding.impl(args, FakeToolContext())
    assert isinstance(out, str) and len(out.encode()) <= binding.spec.max_output_bytes
    data = json.loads(out)
    assert isinstance(data, dict)
    for key in ("tool", "method", "formula_reference", "n", "assumptions", "warnings", "results"):
        assert key in data, key
    assert data["tool"] == binding.spec.name and data["method"] and data["formula_reference"]
    return data
