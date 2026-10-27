"""stats_cluster_bootstrap: subject-clustered percentile bootstrap CI for a diagnostic metric (fixed seed)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
from numpy.typing import NDArray
from researchos.contracts.gateway import ToolBinding, ToolContext

from . import resample
from ._common import (
    CONFIDENCE_SCHEMA,
    Report,
    StatsInputError,
    as_int,
    binary_vector,
    confidence_of,
    finite_vector,
    same_length,
    tool_spec,
)

NAME = "stats_cluster_bootstrap"
MAX_N = 50_000
MAX_RESAMPLES = 20_000
MAX_WORK = 200_000_000  # resamples x samples
F = NDArray[np.float64]
# metric -> (formula, numerator(counts), denominator(counts)); counts columns are tp, fp, fn, tn
_TP, _FP, _FN, _TN = range(4)
COUNT_METRICS: dict[str, tuple[str, Callable[[F], F], Callable[[F], F]]] = {
    "accuracy": ("(TP+TN)/N", lambda c: c[:, _TP] + c[:, _TN], lambda c: c.sum(1)),
    "sensitivity": ("TP/(TP+FN)", lambda c: c[:, _TP], lambda c: c[:, _TP] + c[:, _FN]),
    "specificity": ("TN/(TN+FP)", lambda c: c[:, _TN], lambda c: c[:, _TN] + c[:, _FP]),
    "ppv": ("TP/(TP+FP)", lambda c: c[:, _TP], lambda c: c[:, _TP] + c[:, _FP]),
    "npv": ("TN/(TN+FN)", lambda c: c[:, _TN], lambda c: c[:, _TN] + c[:, _FN]),
    "f1": ("2TP/(2TP+FP+FN)", lambda c: 2 * c[:, _TP], lambda c: 2 * c[:, _TP] + c[:, _FP] + c[:, _FN]),
}
METRICS = (*COUNT_METRICS, "balanced_accuracy", "auc")
_LABELS = {"type": "array", "minItems": 2, "maxItems": MAX_N, "items": {"enum": [0, 1]}}
SPEC = tool_spec(
    NAME,
    "Subject-clustered bootstrap percentile CI for one metric (accuracy, sensitivity, specificity, ppv, npv, f1, "
    "balanced_accuracy from y_pred; auc from scores). Whole clusters (subjects) are resampled with replacement "
    "using a fixed seed; the seed, resamples and number of undefined resamples are reported. Read-only.",
    {
        "metric": {"enum": list(METRICS)},
        "y_true": {**_LABELS, "description": "reference labels, 1 = positive"},
        "y_pred": {**_LABELS, "description": "predicted labels (all metrics except auc)"},
        "scores": {
            "type": "array",
            "minItems": 2,
            "maxItems": MAX_N,
            "items": {"type": "number"},
            "description": "scores, higher = more likely positive (metric auc)",
        },
        "clusters": {
            "type": "array",
            "minItems": 2,
            "maxItems": MAX_N,
            "items": {"type": ["string", "integer"]},
            "description": "subject / cluster id per sample",
        },
        "resamples": {"type": "integer", "minimum": 100, "maximum": MAX_RESAMPLES, "default": 2000},
        "seed": {"type": "integer", "minimum": 0, "maximum": 2**63 - 1, "default": 0},
        "confidence": CONFIDENCE_SCHEMA,
    },
    ("metric", "y_true", "clusters"),
    timeout_s=120.0,
)
ASSUMPTIONS = [
    "Clusters (subjects) are independent; samples within a cluster may be correlated.",
    "Percentile interval over resamples where the metric is defined; undefined resamples are excluded and counted.",
    "The point estimate is computed on the original (non-resampled) data.",
]


def _cluster_index(value: object, n: int) -> tuple[NDArray[np.int64], int]:
    if not isinstance(value, list) or len(value) != n:
        raise StatsInputError("clusters must be a list with one id per sample")
    if any(isinstance(v, bool) or not isinstance(v, str | int) or v == "" for v in value):
        raise StatsInputError("cluster ids must be non-empty strings or integers")
    _, inverse = np.unique(np.asarray([f"{type(v).__name__}:{v}" for v in value]), return_inverse=True)
    groups = int(np.max(inverse)) + 1
    if groups < 2:
        raise StatsInputError("cluster bootstrap needs at least 2 clusters")
    return inverse.astype(np.int64), groups


def _per_cluster_counts(
    y: NDArray[np.int64], p: NDArray[np.int64], idx: NDArray[np.int64], g: int
) -> NDArray[np.int64]:
    cell = np.where(y == 1, np.where(p == 1, _TP, _FN), np.where(p == 1, _FP, _TN))
    return np.bincount(idx * 4 + cell, minlength=g * 4).reshape(g, 4).astype(np.int64)


def _count_metric(metric: str, counts: NDArray[np.int64]) -> F:
    c = counts.astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        if metric == "balanced_accuracy":
            sens = c[:, _TP] / (c[:, _TP] + c[:, _FN])
            spec = c[:, _TN] / (c[:, _TN] + c[:, _FP])
            return np.asarray((sens + spec) / 2.0, dtype=np.float64)
        _, num, den = COUNT_METRICS[metric]
        d = den(c)
        return np.asarray(np.where(d > 0, num(c) / np.where(d > 0, d, 1.0), np.nan), dtype=np.float64)


class _WeightedAuc:
    """AUC = sum_g P_g (N_below_g + N_g / 2) / (P N) over tied-score groups, for many sample-weight rows."""

    def __init__(self, y: NDArray[np.int64], scores: F, idx: NDArray[np.int64]) -> None:
        order = np.argsort(scores, kind="stable")
        s = scores[order]
        self.starts = np.flatnonzero(np.r_[True, s[1:] != s[:-1]])
        self.y = y[order].astype(np.float64)
        self.idx = idx[order]

    def __call__(self, w_clusters: NDArray[np.int64]) -> F:
        w = w_clusters[:, self.idx].astype(np.float64)
        pos = np.add.reduceat(w * self.y, self.starts, axis=1)
        neg = np.add.reduceat(w * (1.0 - self.y), self.starts, axis=1)
        below = np.cumsum(neg, axis=1) - neg
        den = pos.sum(1) * neg.sum(1)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.asarray(np.where(den > 0, (pos * (below + neg / 2.0)).sum(1) / den, np.nan), dtype=np.float64)


def _inputs(args: Mapping[str, Any]) -> tuple[str, NDArray[np.int64], NDArray[Any], NDArray[np.int64], int]:
    metric = args.get("metric")
    if metric not in METRICS:
        raise StatsInputError(f"metric must be one of {', '.join(METRICS)}")
    y = binary_vector(args.get("y_true"), "y_true", min_len=2, max_len=MAX_N)
    if metric == "auc":
        if "y_pred" in args:
            raise StatsInputError("metric auc uses scores, not y_pred")
        x: NDArray[Any] = finite_vector(args.get("scores"), "scores", min_len=2, max_len=MAX_N)
    else:
        if "scores" in args:
            raise StatsInputError(f"metric {metric} uses y_pred, not scores")
        x = binary_vector(args.get("y_pred"), "y_pred", min_len=2, max_len=MAX_N)
    n = same_length(y_true=y, predictions=x)
    idx, groups = _cluster_index(args.get("clusters"), n)
    return metric, y, x, idx, groups


def run(args: Mapping[str, Any]) -> str:
    conf = confidence_of(args)
    metric, y, x, idx, groups = _inputs(args)
    seed = as_int(args.get("seed", 0), "seed", maximum=2**63 - 1)
    reps = as_int(args.get("resamples", 2000), "resamples", minimum=100, maximum=MAX_RESAMPLES)
    if metric == "auc" and reps * len(y) > MAX_WORK:
        raise StatsInputError(f"resamples x samples must be <= {MAX_WORK} for auc; lower resamples")
    rep = Report(
        NAME, f"Cluster (subject) bootstrap percentile CI of {metric}", resample.REFERENCE, len(y), list(ASSUMPTIONS)
    )
    stat: Callable[[NDArray[np.int64]], F]
    if metric == "auc":
        stat, cost, formula = _WeightedAuc(y, x, idx), len(y), "P(score_pos > score_neg) + 0.5 P(tie)"
    else:
        per = _per_cluster_counts(y, x, idx, groups)
        stat, cost = (lambda w: _count_metric(metric, w @ per)), groups * 4
        formula = COUNT_METRICS[metric][0] if metric in COUNT_METRICS else "(sensitivity+specificity)/2"
    point = float(stat(np.ones((1, groups), dtype=np.int64))[0])
    if not np.isfinite(point):
        rep.warn(f"{metric} is undefined on the original data (empty class); estimate and CI reported as null")
        return rep.emit(
            {"metric": metric, "estimate": None, "formula": formula, "bootstrap": None},
            n_clusters=groups,
            confidence=conf,
        )
    values = np.concatenate([stat(w) for w in resample.weights(seed, groups, reps, max(cost, groups))])
    boot = resample.summary(values, seed, reps, conf)
    if boot["resamples_undefined"]:
        rep.warn(f"{boot['resamples_undefined']} of {reps} resamples had an undefined {metric} and were excluded")
    return rep.emit(
        {"metric": metric, "estimate": point, "formula": formula, "bootstrap": boot}, n_clusters=groups, confidence=conf
    )


def _impl(args: dict[str, Any], ctx: ToolContext) -> str:
    return run(args)


BINDING = ToolBinding(spec=SPEC, impl=_impl)
