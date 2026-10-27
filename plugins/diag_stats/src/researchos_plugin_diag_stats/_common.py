"""Shared strict validation, undefined-safe arithmetic and the JSON result envelope of every tool.

Every tool returns one JSON object: tool, method, formula_reference, n, assumptions, warnings, results (+ extras).
Undefined quantities (0/0, empty class, zero variance) are null plus a warning, never 0. Invalid input raises
StatsInputError (a ValueError) with a precise message; nothing is coerced silently.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence, Sized
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray
from researchos.contracts.gateway import ToolError, ToolSpec
from scipy import stats

MAX_OUTPUT_BYTES = 64_000
CONFIDENCE_SCHEMA: dict[str, Any] = {
    "type": "number",
    "exclusiveMinimum": 0,
    "exclusiveMaximum": 1,
    "default": 0.95,
    "description": "Confidence level of every interval (two-sided).",
}


class StatsInputError(ToolError, ValueError):
    """Invalid tool input: wrong shape, label, NaN/inf, negative count, empty input ... (core 1.3: a ToolError, so
    the host returns `ERROR [invalid_input]: ...` to the model instead of failing the step)."""

    def __init__(self, message: str) -> None:
        super().__init__("invalid_input", message)


def tool_spec(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: Sequence[str] = (),
    *,
    timeout_s: float = 30.0,
    extra: Mapping[str, Any] | None = None,
) -> ToolSpec:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    schema.update(extra or {})
    return ToolSpec(
        name=name,
        description=description,
        params_schema=schema,
        read_only=True,
        destructive=False,
        side_effect_scope="none",
        timeout_s=timeout_s,
        max_output_bytes=MAX_OUTPUT_BYTES,
        needs=(),
        derived_from_args=True,  # pure functions of the arguments: no stored data enters the output (ADR-171)
    )


@dataclass
class Report:
    tool: str
    method: str
    formula_reference: str
    n: int
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def ratio(self, num: float, den: float, what: str) -> float | None:
        """num/den, or None + warning when den == 0 (undefined, never reported as 0)."""
        if den == 0:
            self.warn(f"{what} is undefined (denominator 0); reported as null")
            return None
        return num / den

    def emit(self, results: Mapping[str, Any], **extra: Any) -> str:
        payload = {
            "tool": self.tool,
            "method": self.method,
            "formula_reference": self.formula_reference,
            "n": self.n,
            "assumptions": self.assumptions,
            "warnings": self.warnings,
            **extra,
            "results": results,
        }
        text = json.dumps(payload, allow_nan=False, ensure_ascii=False, separators=(",", ":"), default=_plain)
        if len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise StatsInputError(f"result exceeds {MAX_OUTPUT_BYTES} bytes; split the request into smaller calls")
        return text


def _plain(obj: object) -> object:
    if isinstance(obj, np.generic):
        return obj.item()
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def z_crit(confidence: float) -> float:
    """Two-sided standard-normal critical value, as statsmodels: norm.isf(alpha / 2)."""
    return float(stats.norm.isf((1.0 - confidence) / 2.0))


def interval(lo: float, hi: float) -> list[float]:
    return [float(lo), float(hi)]


def confidence_of(args: Mapping[str, Any]) -> float:
    c = args.get("confidence", 0.95)
    if isinstance(c, bool) or not isinstance(c, int | float) or not 0.0 < float(c) < 1.0:
        raise StatsInputError("confidence must be a number strictly between 0 and 1")
    return float(c)


def as_int(value: object, what: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StatsInputError(f"{what} must be an integer")
    if isinstance(value, float) and not (math.isfinite(value) and value.is_integer()):
        raise StatsInputError(f"{what} must be an integer, got {value!r}")
    v = int(value)
    if v < minimum or (maximum is not None and v > maximum):
        upper = "" if maximum is None else f" and <= {maximum}"
        raise StatsInputError(f"{what} must be >= {minimum}{upper}, got {v}")
    return v


def count_matrix(
    value: object, what: str, *, square: bool, min_rows: int = 2, min_cols: int = 2, max_dim: int = 2000
) -> NDArray[np.int64]:
    """Rectangular matrix of non-negative integer counts."""
    if not isinstance(value, list) or not all(isinstance(r, list) for r in value):
        raise StatsInputError(f"{what} must be a list of rows (lists of integer counts)")
    widths = {len(r) for r in value}
    if len(widths) != 1:
        raise StatsInputError(f"{what} rows must all have the same length")
    rows, cols = len(value), widths.pop()
    if rows < min_rows or cols < min_cols or rows > max_dim or cols > max_dim:
        raise StatsInputError(
            f"{what} must be at least {min_rows}x{min_cols} (and at most {max_dim}), got {rows}x{cols}"
        )
    if square and rows != cols:
        raise StatsInputError(f"{what} must be square, got {rows}x{cols}")
    out = np.array(
        [[as_int(x, f"{what}[{i}][{j}]") for j, x in enumerate(r)] for i, r in enumerate(value)], dtype=np.int64
    )
    if int(out.sum()) == 0:
        raise StatsInputError(f"{what} has no observations (all counts are 0)")
    return out


def binary_vector(value: object, what: str, *, min_len: int = 1, max_len: int = 50_000) -> NDArray[np.int64]:
    if not isinstance(value, list) or not min_len <= len(value) <= max_len:
        raise StatsInputError(f"{what} must be a list of 0/1 labels with {min_len}..{max_len} entries")
    if any(isinstance(v, bool) or v not in (0, 1) for v in value):
        raise StatsInputError(f"{what} may only contain the integers 0 and 1 (1 = positive)")
    return np.asarray(value, dtype=np.int64)


def finite_vector(value: object, what: str, *, min_len: int = 1, max_len: int = 50_000) -> NDArray[np.float64]:
    if not isinstance(value, list) or not min_len <= len(value) <= max_len:
        raise StatsInputError(f"{what} must be a list of numbers with {min_len}..{max_len} entries")
    if any(isinstance(v, bool) or not isinstance(v, int | float) for v in value):
        raise StatsInputError(f"{what} may only contain numbers")
    arr = np.asarray(value, dtype=np.float64)
    if not np.isfinite(arr).all():
        raise StatsInputError(f"{what} contains NaN or infinite values")
    return arr


def same_length(**arrays: Sized) -> int:
    lengths = {k: len(v) for k, v in arrays.items()}
    if len(set(lengths.values())) != 1:
        raise StatsInputError(f"inputs must have equal length, got {lengths}")
    return next(iter(lengths.values()))


def labels_of(value: object, k: int, default_prefix: str = "class_") -> list[str]:
    if value is None:
        return [f"{default_prefix}{i}" for i in range(k)]
    if not isinstance(value, list) or len(value) != k or not all(isinstance(v, str) and v for v in value):
        raise StatsInputError(f"labels must be {k} non-empty strings (one per row/column)")
    if len(set(value)) != k:
        raise StatsInputError("labels must be unique")
    return list(value)


def p_text(p: float) -> str:
    """APA p-value: three decimals without a leading zero, or "p < .001"."""
    return "p < .001" if p < 0.001 else f"p = {p:.3f}".replace("0.", ".", 1)


def apa(symbol: str, df: str, stat: float, p: float, effect: Mapping[str, Any], conf: float) -> str:
    out = f"{symbol}{f'({df})' if df else ''} = {stat:.2f}, {p_text(p)}"
    if effect.get("value") is not None:
        out += f", {str(effect['name']).replace('_', ' ')} = {effect['value']:.2f}"
        ci = effect.get("ci")
        if isinstance(ci, list):
            out += f", {round(conf * 100)}% CI [{ci[0]:.2f}, {ci[1]:.2f}]"
    return out
