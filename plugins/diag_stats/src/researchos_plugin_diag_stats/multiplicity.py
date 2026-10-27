"""stats_multiplicity: Holm step-down (FWER) and Benjamini-Hochberg step-up (FDR) adjusted p-values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
from numpy.typing import NDArray
from researchos.contracts.gateway import ToolBinding, ToolContext

from ._common import Report, StatsInputError, finite_vector, tool_spec

NAME = "stats_multiplicity"
MAX_P = 200
METHODS = ("holm", "bh")
REFS = {"holm": "Holm S (1979) Scand J Stat 6:65-70", "bh": "Benjamini Y, Hochberg Y (1995) J R Stat Soc B 57:289-300"}
SPEC = tool_spec(
    NAME,
    "Adjust a family of p-values for multiple comparisons: Holm step-down (family-wise error rate) and/or "
    "Benjamini-Hochberg step-up (false discovery rate). Returns adjusted p-values (input order) and rejections at "
    "alpha. Read-only.",
    {
        "p_values": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_P,
            "items": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "labels": {"type": "array", "maxItems": MAX_P, "items": {"type": "string", "minLength": 1, "maxLength": 64}},
        "methods": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"enum": list(METHODS)},
            "default": list(METHODS),
        },
        "alpha": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1, "default": 0.05},
    },
    ("p_values",),
)


def holm(p: NDArray[np.float64]) -> NDArray[np.float64]:
    m = p.size
    order = np.argsort(p, kind="stable")
    adj = np.minimum(1.0, np.maximum.accumulate((m - np.arange(m)) * p[order]))
    out = np.empty(m)
    out[order] = adj
    return out


def bh(p: NDArray[np.float64]) -> NDArray[np.float64]:
    m = p.size
    order = np.argsort(p, kind="stable")[::-1]
    ranks = np.arange(m, 0, -1)
    adj = np.minimum(1.0, np.minimum.accumulate(m * p[order] / ranks))
    out = np.empty(m)
    out[order] = adj
    return out


def _args(args: Mapping[str, Any]) -> tuple[NDArray[np.float64], list[str] | None, list[str], float]:
    p = finite_vector(args.get("p_values"), "p_values", min_len=1, max_len=MAX_P)
    if ((p < 0) | (p > 1)).any():
        raise StatsInputError("p_values must lie in [0, 1]")
    labels = args.get("labels")
    if labels is not None and (
        not isinstance(labels, list) or len(labels) != p.size or not all(isinstance(v, str) and v for v in labels)
    ):
        raise StatsInputError("labels must be one non-empty string per p-value")
    methods = args.get("methods", list(METHODS))
    if (
        not isinstance(methods, list)
        or not methods
        or any(m not in METHODS for m in methods)
        or len(set(methods)) != len(methods)
    ):
        raise StatsInputError(f"methods must be a non-empty list of distinct values from {METHODS}")
    alpha = args.get("alpha", 0.05)
    if isinstance(alpha, bool) or not isinstance(alpha, int | float) or not 0.0 < float(alpha) < 1.0:
        raise StatsInputError("alpha must be a number strictly between 0 and 1")
    return p, labels, methods, float(alpha)


def run(args: Mapping[str, Any]) -> str:
    p, labels, methods, alpha = _args(args)
    rep = Report(
        NAME,
        " + ".join({"holm": "Holm step-down", "bh": "Benjamini-Hochberg step-up"}[m] for m in methods),
        "; ".join(REFS[m] for m in methods),
        int(p.size),
    )
    rep.assumptions += [
        "Holm controls the FWER under any dependence.",
        "BH controls the FDR under independence or positive regression dependence (PRDS).",
    ]
    if p.size == 1:
        rep.warn("a single p-value: adjustment leaves it unchanged")
    results: dict[str, Any] = {}
    for m in methods:
        adj = holm(p) if m == "holm" else bh(p)
        results[m] = {
            "adjusted": [float(v) for v in adj],
            "reject": [bool(v <= alpha) for v in adj],
            "n_rejected": int((adj <= alpha).sum()),
        }
    return rep.emit(results, alpha=alpha, p_values=[float(v) for v in p], labels=labels)


def _impl(args: dict[str, Any], ctx: ToolContext) -> str:
    return run(args)


BINDING = ToolBinding(spec=SPEC, impl=_impl)
