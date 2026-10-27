"""stats_roc_auc: ROC AUC with DeLong variance / CI, and the DeLong paired test for two models on the same cases."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
from numpy.typing import NDArray
from researchos.contracts.gateway import ToolBinding, ToolContext
from scipy import stats

from ._common import (
    CONFIDENCE_SCHEMA,
    Report,
    binary_vector,
    confidence_of,
    finite_vector,
    interval,
    same_length,
    tool_spec,
    z_crit,
)

NAME = "stats_roc_auc"
MAX_N = 50_000
AUC_FORMULA = "P(score_pos > score_neg) + 0.5 P(tie)"
REFERENCE = (
    "DeLong ER, DeLong DM, Clarke-Pearson DL (1988) Biometrics 44:837-845; fast midrank algorithm: "
    "Sun X, Xu W (2014) IEEE Signal Process Lett 21:1389-1393"
)
_SCORES = {"type": "array", "minItems": 2, "maxItems": MAX_N, "items": {"type": "number"}}
SPEC = tool_spec(
    NAME,
    "ROC AUC (Mann-Whitney, ties count 1/2) with DeLong standard error and normal CI; with scores_b also the DeLong "
    "paired test (AUC_a - AUC_b, z, two-sided p) for two models scored on the same cases. Higher score = more "
    "likely positive. Undefined values (one class only, zero variance) are null with a warning. Read-only.",
    {
        "y_true": {
            "type": "array",
            "minItems": 2,
            "maxItems": MAX_N,
            "items": {"enum": [0, 1]},
            "description": "reference labels, 1 = positive",
        },
        "scores": {**_SCORES, "description": "model A scores, same order as y_true"},
        "scores_b": {**_SCORES, "description": "optional model B scores for the paired DeLong test"},
        "confidence": CONFIDENCE_SCHEMA,
    },
    ("y_true", "scores"),
)
ASSUMPTIONS = [
    "Cases are independent (one score per subject); for repeated measures use stats_cluster_bootstrap (metric auc).",
    "CI is the normal approximation AUC +/- z*SE(DeLong), clipped to [0, 1] (clipping is reported as a warning).",
    "Tied scores between a positive and a negative case count 1/2.",
]


def _midrank(x: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.asarray(stats.rankdata(x, method="average"), dtype=np.float64)


def delong(y: NDArray[np.int64], scores: NDArray[np.float64]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """AUCs (k,) and DeLong covariance (k, k) for scores of shape (k, n); needs >= 2 positives and negatives."""
    pos, neg = scores[:, y == 1], scores[:, y == 0]
    m, n = pos.shape[1], neg.shape[1]
    v10 = np.empty((scores.shape[0], m))
    v01 = np.empty((scores.shape[0], n))
    aucs = np.empty(scores.shape[0])
    for r in range(scores.shape[0]):
        tz = _midrank(np.concatenate([pos[r], neg[r]]))
        tx, ty = _midrank(pos[r]), _midrank(neg[r])
        aucs[r] = (tz[:m].sum() - m * (m + 1) / 2.0) / (m * n)
        v10[r] = (tz[:m] - tx) / n
        v01[r] = 1.0 - (tz[m:] - ty) / m
    cov = np.atleast_2d(np.cov(v10, ddof=1)) / m + np.atleast_2d(np.cov(v01, ddof=1)) / n
    return aucs, cov


def _ci(
    rep: Report, what: str, value: float, var: float, conf: float, bounds: tuple[float, float]
) -> tuple[float | None, list[float] | None]:
    if not var > 0.0:
        rep.warn(f"{what}: DeLong variance is 0 (e.g. perfect separation); CI is undefined and reported as null")
        return None, None
    se = math.sqrt(var)
    lo, hi = value - z_crit(conf) * se, value + z_crit(conf) * se
    if lo < bounds[0] or hi > bounds[1]:
        rep.warn(f"{what}: normal CI clipped to [{bounds[0]:g}, {bounds[1]:g}]")
    return se, interval(max(bounds[0], lo), min(bounds[1], hi))


def _single(rep: Report, label: str, auc: float, var: float, conf: float) -> dict[str, Any]:
    se, ci = _ci(rep, f"AUC {label}", auc, var, conf, (0.0, 1.0))
    return {"auc": auc, "se": se, "ci": ci, "formula": AUC_FORMULA}


def _paired(rep: Report, aucs: NDArray[np.float64], cov: NDArray[np.float64], conf: float) -> dict[str, Any]:
    diff = float(aucs[0] - aucs[1])
    var = float(cov[0, 0] + cov[1, 1] - 2.0 * cov[0, 1])
    se, ci = _ci(rep, "AUC difference", diff, var, conf, (-1.0, 1.0))
    z = None if se is None else diff / se
    if z is None:
        rep.warn("DeLong paired test is undefined (variance of the difference is 0); z and p reported as null")
    p = None if z is None else float(2.0 * stats.norm.sf(abs(z)))
    return {
        "difference": diff,
        "se": se,
        "ci": ci,
        "z": z,
        "p_value": p,
        "covariance": float(cov[0, 1]),
        "formula": "z = (AUC_a - AUC_b) / sqrt(V_a + V_b - 2 Cov_ab)",
    }


def run(args: Mapping[str, Any]) -> str:
    conf = confidence_of(args)
    y = binary_vector(args.get("y_true"), "y_true", min_len=2, max_len=MAX_N)
    rows = [finite_vector(args.get("scores"), "scores", min_len=2, max_len=MAX_N)]
    if args.get("scores_b") is not None:
        rows.append(finite_vector(args.get("scores_b"), "scores_b", min_len=2, max_len=MAX_N))
    same_length(y_true=y, **{f"scores{'_b' if i else ''}": r for i, r in enumerate(rows)})
    m, n = int(y.sum()), int(len(y) - y.sum())
    method = "DeLong AUC" + (" + paired DeLong test" if len(rows) == 2 else "")
    rep = Report(NAME, method, REFERENCE, len(y), list(ASSUMPTIONS))
    counts = {"n_positive": m, "n_negative": n}
    if m == 0 or n == 0:
        rep.warn("AUC is undefined: y_true contains only one class; all results reported as null")
        empty = {"auc": None, "se": None, "ci": None}
        return rep.emit(
            {"model_a": empty, **({"model_b": empty, "paired": None} if len(rows) == 2 else {})},
            confidence=conf,
            **counts,
        )
    return rep.emit(_results(rep, y, rows, conf, m, n), confidence=conf, **counts)


def _results(
    rep: Report, y: NDArray[np.int64], rows: list[NDArray[np.float64]], conf: float, m: int, n: int
) -> dict[str, Any]:
    labels = ("a", "b")[: len(rows)]
    if m < 2 or n < 2:
        rep.warn("DeLong variance needs >= 2 positive and >= 2 negative cases; SE/CI reported as null")
        plain = [(_midrank(r)[y == 1].sum() - m * (m + 1) / 2.0) / (m * n) for r in rows]
        out: dict[str, Any] = {
            f"model_{lab}": {"auc": float(a), "se": None, "ci": None, "formula": AUC_FORMULA}
            for lab, a in zip(labels, plain, strict=True)
        }
        if len(rows) == 2:
            out["paired"] = None
        return out
    aucs, cov = delong(y, np.vstack(rows))
    out = {f"model_{lab}": _single(rep, lab, float(aucs[i]), float(cov[i, i]), conf) for i, lab in enumerate(labels)}
    if len(rows) == 2:
        out["paired"] = _paired(rep, aucs, cov, conf)
    return out


def _impl(args: dict[str, Any], ctx: ToolContext) -> str:
    return run(args)


BINDING = ToolBinding(spec=SPEC, impl=_impl)
