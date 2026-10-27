"""stats_diag_metrics: sensitivity, specificity, PPV, NPV, accuracy, F1, balanced accuracy, LR+/LR- from a 2x2
table or (one-vs-rest) from a KxK confusion matrix, with Wilson and exact Clopper-Pearson intervals."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from researchos.contracts.gateway import ToolBinding, ToolContext

from ._common import (
    CONFIDENCE_SCHEMA,
    Report,
    StatsInputError,
    as_int,
    confidence_of,
    count_matrix,
    labels_of,
    tool_spec,
)
from .proportions import CLOPPER_PEARSON_REF, LR_REF, WILSON_REF, clopper_pearson, log_ratio_ci, wilson

NAME = "stats_diag_metrics"
CELLS = ("tp", "fp", "fn", "tn")
MAX_CLASSES = 20
_COUNT = {"type": "integer", "minimum": 0}
SPEC = tool_spec(
    NAME,
    "Diagnostic accuracy from a 2x2 table (tp, fp, fn, tn) or a KxK confusion matrix (rows = reference class, "
    "columns = predicted class; one-vs-rest per class): sensitivity, specificity, PPV, NPV, accuracy (Wilson and "
    "Clopper-Pearson CIs), F1, balanced accuracy, LR+ and LR- (log-method CIs). Undefined values are null with a "
    "warning. Read-only; no side effects.",
    {
        **{c: {**_COUNT, "description": f"{c.upper()} count (2x2 mode)"} for c in CELLS},
        "confusion": {
            "type": "array",
            "minItems": 2,
            "maxItems": MAX_CLASSES,
            "items": {"type": "array", "minItems": 2, "maxItems": MAX_CLASSES, "items": _COUNT},
            "description": "KxK counts, rows = reference (true) class, columns = predicted class",
        },
        "labels": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 64},
            "maxItems": MAX_CLASSES,
            "description": "class names for the confusion matrix rows/columns",
        },
        "confidence": CONFIDENCE_SCHEMA,
    },
    extra={"anyOf": [{"required": list(CELLS)}, {"required": ["confusion"]}]},
)
ASSUMPTIONS = [
    "Each count is one independent observation (one per subject); for clustered data use stats_cluster_bootstrap.",
    "Proportion CIs: Wilson score (no continuity correction) and exact Clopper-Pearson.",
    "F1 and balanced accuracy are reported without a closed-form CI (use stats_cluster_bootstrap).",
    "LR CIs use the log method and are undefined when any cell involved is 0 (no continuity correction).",
]


@dataclass(frozen=True)
class Cells:
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn


def _proportion(rep: Report, what: str, k: int, d: int, formula: str, conf: float) -> dict[str, Any]:
    return {
        "value": rep.ratio(k, d, what),
        "numerator": k,
        "denominator": d,
        "formula": formula,
        "ci": {"wilson": wilson(k, d, conf), "clopper_pearson": clopper_pearson(k, d, conf)},
    }


def _lr(rep: Report, what: str, a: int, n_a: int, b: int, n_b: int, formula: str, conf: float) -> dict[str, Any]:
    """Ratio (a/n_a)/(b/n_b); undefined when n_a, n_b or b is 0."""
    value: float | None = None
    if n_a == 0 or n_b == 0:
        rep.warn(f"{what} is undefined (an empty reference class); reported as null")
    elif b == 0:
        rep.warn(f"{what} is undefined (division by a zero proportion); reported as null")
    else:
        value = (a / n_a) / (b / n_b)
    ci = None if value is None else log_ratio_ci(value, a, n_a, b, n_b, conf)
    if value is not None and ci is None:
        rep.warn(f"{what} CI is undefined (zero cell); reported as null")
    return {"value": value, "formula": formula, "ci": {"log_method": ci}}


def binary_metrics(c: Cells, conf: float, rep: Report, prefix: str = "") -> dict[str, Any]:
    pos, neg = c.tp + c.fn, c.fp + c.tn
    out: dict[str, Any] = {"counts": {"tp": c.tp, "fp": c.fp, "fn": c.fn, "tn": c.tn, "n": c.n}}
    out["sensitivity"] = _proportion(rep, f"{prefix}sensitivity", c.tp, pos, "TP/(TP+FN)", conf)
    out["specificity"] = _proportion(rep, f"{prefix}specificity", c.tn, neg, "TN/(TN+FP)", conf)
    out["ppv"] = _proportion(rep, f"{prefix}PPV", c.tp, c.tp + c.fp, "TP/(TP+FP)", conf)
    out["npv"] = _proportion(rep, f"{prefix}NPV", c.tn, c.tn + c.fn, "TN/(TN+FN)", conf)
    out["accuracy"] = _proportion(rep, f"{prefix}accuracy", c.tp + c.tn, c.n, "(TP+TN)/N", conf)
    out["f1"] = {
        "value": rep.ratio(2 * c.tp, 2 * c.tp + c.fp + c.fn, f"{prefix}F1"),
        "formula": "2TP/(2TP+FP+FN)",
        "ci": None,
    }
    sens, spec = out["sensitivity"]["value"], out["specificity"]["value"]
    if sens is None or spec is None:
        rep.warn(f"{prefix}balanced accuracy is undefined (sensitivity or specificity undefined); reported as null")
    out["balanced_accuracy"] = {
        "value": None if sens is None or spec is None else (sens + spec) / 2.0,
        "formula": "(sensitivity+specificity)/2",
        "ci": None,
    }
    out["lr_positive"] = _lr(rep, f"{prefix}LR+", c.tp, pos, c.fp, neg, "sensitivity/(1-specificity)", conf)
    out["lr_negative"] = _lr(rep, f"{prefix}LR-", c.fn, pos, c.tn, neg, "(1-sensitivity)/specificity", conf)
    return out


def _cells(args: Mapping[str, Any]) -> Cells:
    c = Cells(*(as_int(args.get(k), k) for k in CELLS))
    if c.n == 0:
        raise StatsInputError("the 2x2 table has no observations (all counts are 0)")
    return c


def _one_vs_rest(m: NDArray[np.int64], i: int) -> Cells:
    tp = int(m[i, i])
    fn, fp = int(m[i].sum()) - tp, int(m[:, i].sum()) - tp
    return Cells(tp, fp, fn, int(m.sum()) - tp - fn - fp)


def _macro(per_class: dict[str, Any], key: str, rep: Report, what: str) -> float | None:
    values = [v[key]["value"] for v in per_class.values()]
    if any(v is None for v in values):
        rep.warn(f"{what} is undefined (a per-class value is undefined); reported as null")
        return None
    return float(np.mean(values))


def confusion_metrics(m: NDArray[np.int64], labels: list[str], conf: float, rep: Report) -> dict[str, Any]:
    for i, label in enumerate(labels):
        if int(m[i].sum()) == 0:
            rep.warn(f"class {label} has no reference (true) observations (empty class)")
    per_class = {lab: binary_metrics(_one_vs_rest(m, i), conf, rep, f"class {lab}: ") for i, lab in enumerate(labels)}
    n, correct = int(m.sum()), int(np.trace(m))
    overall = {
        "accuracy": _proportion(rep, "overall accuracy", correct, n, "trace(C)/N", conf),
        "macro_sensitivity": {
            "value": _macro(per_class, "sensitivity", rep, "macro sensitivity"),
            "formula": "mean of per-class sensitivity (multi-class balanced accuracy)",
        },
        "macro_f1": {"value": _macro(per_class, "f1", rep, "macro F1"), "formula": "mean of per-class F1"},
    }
    return {"labels": labels, "overall": overall, "per_class": per_class}


def run(args: Mapping[str, Any]) -> str:
    conf = confidence_of(args)
    has_cells, has_matrix = any(k in args for k in CELLS), "confusion" in args
    if has_cells == has_matrix:
        raise StatsInputError("give either all of tp, fp, fn, tn or a confusion matrix (exactly one form)")
    refs = f"{WILSON_REF}; {CLOPPER_PEARSON_REF}; {LR_REF}"
    if has_cells:
        if "labels" in args:
            raise StatsInputError("labels only apply to the confusion-matrix form")
        c = _cells(args)
        rep = Report(NAME, "Diagnostic accuracy from a 2x2 table", refs, c.n, list(ASSUMPTIONS))
        return rep.emit(binary_metrics(c, conf, rep), confidence=conf)
    m = count_matrix(args["confusion"], "confusion", square=True, max_dim=MAX_CLASSES)
    labels = labels_of(args.get("labels"), m.shape[0])
    rep = Report(
        NAME,
        "One-vs-rest diagnostic accuracy from a KxK confusion matrix (rows = reference class)",
        refs,
        int(m.sum()),
        list(ASSUMPTIONS),
    )
    return rep.emit(confusion_metrics(m, labels, conf, rep), confidence=conf)


def _impl(args: dict[str, Any], ctx: ToolContext) -> str:
    return run(args)


BINDING = ToolBinding(spec=SPEC, impl=_impl)
