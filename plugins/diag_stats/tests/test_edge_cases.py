"""Edge cases: perfect classifier, all one class, zero cells, tiny n, tied scores, strict validation, bootstrap
reproducibility (bit-for-bit) and an independent re-implementation of the cluster bootstrap."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest
from researchos.sdk.testing import FakeToolContext

import researchos_plugin_diag_stats.bootstrap as bootstrap
import researchos_plugin_diag_stats.diag_metrics as diag_metrics
import researchos_plugin_diag_stats.multiplicity as multiplicity
import researchos_plugin_diag_stats.paired as paired
import researchos_plugin_diag_stats.roc as roc
from helpers import call
from researchos_plugin_diag_stats._common import StatsInputError


def _diag(**cells: int) -> dict[str, Any]:
    return call(diag_metrics.BINDING, dict(cells))


def test_perfect_classifier() -> None:
    d = _diag(tp=20, fp=0, fn=0, tn=30)
    r = d["results"]
    assert r["sensitivity"]["value"] == 1.0 and r["specificity"]["value"] == 1.0
    assert r["sensitivity"]["ci"]["clopper_pearson"][1] == 1.0 and r["sensitivity"]["ci"]["wilson"][1] == 1.0
    assert r["lr_positive"]["value"] is None and r["lr_negative"]["value"] == 0.0
    assert r["lr_negative"]["ci"]["log_method"] is None
    assert any("LR+" in w for w in d["warnings"]) and any("LR- CI" in w for w in d["warnings"])
    auc = call(roc.BINDING, {"y_true": [0, 0, 0, 1, 1, 1], "scores": [0.1, 0.2, 0.3, 0.7, 0.8, 0.9]})
    assert auc["results"]["model_a"]["auc"] == 1.0 and auc["results"]["model_a"]["ci"] is None
    assert any("variance is 0" in w for w in auc["warnings"])


def test_all_one_class_gives_null_never_zero() -> None:
    d = _diag(tp=12, fp=0, fn=3, tn=0)
    r = d["results"]
    assert r["specificity"]["value"] is None
    assert r["npv"]["value"] == 0.0  # TN/(TN+FN) = 0/3 is defined (a real 0), unlike 0/0
    assert r["specificity"]["ci"]["wilson"] is None and r["balanced_accuracy"]["value"] is None
    assert r["lr_positive"]["value"] is None and r["lr_negative"]["value"] is None
    assert any("specificity is undefined" in w for w in d["warnings"])
    auc = call(roc.BINDING, {"y_true": [1, 1, 1], "scores": [0.2, 0.4, 0.9]})
    assert auc["results"]["model_a"]["auc"] is None and auc["warnings"]
    boot = call(
        bootstrap.BINDING,
        {
            "metric": "specificity",
            "y_true": [1, 1, 1, 1],
            "y_pred": [1, 0, 1, 1],
            "clusters": ["a", "a", "b", "c"],
            "resamples": 100,
        },
    )
    assert boot["results"]["estimate"] is None and boot["results"]["bootstrap"] is None


def test_zero_cells_and_empty_class_in_confusion() -> None:
    d = call(diag_metrics.BINDING, {"confusion": [[5, 0, 0], [0, 0, 0], [1, 0, 4]], "labels": ["a", "b", "c"]})
    b = d["results"]["per_class"]["b"]
    assert b["sensitivity"]["value"] is None and b["ppv"]["value"] is None and b["f1"]["value"] is None
    assert d["results"]["overall"]["macro_sensitivity"]["value"] is None
    assert any("class b has no reference" in w for w in d["warnings"])
    assert d["results"]["per_class"]["a"]["specificity"]["value"] == 0.8  # one c case predicted as a
    assert d["results"]["per_class"]["c"]["specificity"]["value"] == 1.0


def test_tiny_n() -> None:
    d = _diag(tp=1, fp=0, fn=0, tn=0)
    r = d["results"]
    assert r["sensitivity"]["value"] == 1.0 and r["specificity"]["value"] is None
    lo, hi = r["sensitivity"]["ci"]["clopper_pearson"]
    assert 0.0 < lo < 0.03 and hi == 1.0
    auc = call(roc.BINDING, {"y_true": [0, 1], "scores": [0.3, 0.6]})
    assert auc["results"]["model_a"]["auc"] == 1.0 and auc["results"]["model_a"]["se"] is None
    m = call(paired.BINDING, {"test": "mcnemar", "table": [[0, 1], [0, 0]]})
    assert m["results"]["p_exact"] == 1.0


def test_ties_in_scores_count_half() -> None:
    auc = call(roc.BINDING, {"y_true": [0, 0, 1, 1], "scores": [0.5, 0.5, 0.5, 0.5]})
    assert auc["results"]["model_a"]["auc"] == 0.5
    auc = call(roc.BINDING, {"y_true": [0, 1, 0, 1], "scores": [1.0, 1.0, 0.0, 2.0]})
    assert auc["results"]["model_a"]["auc"] == pytest.approx(0.875, rel=1e-12)


def test_paired_undefined_cases() -> None:
    m = call(paired.BINDING, {"test": "mcnemar", "table": [[5, 0], [0, 7]]})
    assert m["results"]["p_exact"] == 1.0 and m["results"]["p_mid"] == 1.0 and m["warnings"]  # review #12
    k = call(paired.BINDING, {"test": "cohen_kappa", "table": [[9, 0], [0, 0]]})
    assert k["results"]["kappa"] is None and k["warnings"]
    k = call(paired.BINDING, {"test": "cohen_kappa", "table": [[6, 0], [0, 4]]})
    assert k["results"]["kappa"] == 1.0 and k["results"]["ci"] is None
    f = call(paired.BINDING, {"test": "fleiss_kappa", "table": [[3, 0], [3, 0]], "resamples": 100})
    assert f["results"]["kappa"] is None


@pytest.mark.parametrize(
    ("binding", "args", "message"),
    [
        (diag_metrics.BINDING, {"tp": 1, "fp": 1, "fn": 1}, "tn must be an integer"),
        (diag_metrics.BINDING, {"tp": 1, "fp": -1, "fn": 1, "tn": 1}, ">= 0"),
        (diag_metrics.BINDING, {"tp": 0, "fp": 0, "fn": 0, "tn": 0}, "no observations"),
        (diag_metrics.BINDING, {"tp": 1, "fp": 1, "fn": 1, "tn": 1, "confusion": [[1, 0], [0, 1]]}, "exactly one"),
        (diag_metrics.BINDING, {"confusion": [[1, 0], [0, 1, 2]]}, "same length"),
        (diag_metrics.BINDING, {"confusion": [[1, 0, 0], [0, 1, 0]]}, "square"),
        (diag_metrics.BINDING, {"confusion": [[1, 0], [0, 1]], "labels": ["a", "a"]}, "unique"),
        (diag_metrics.BINDING, {"tp": 1.5, "fp": 1, "fn": 1, "tn": 1}, "integer"),
        (diag_metrics.BINDING, {"tp": 1, "fp": 1, "fn": 1, "tn": 1, "confidence": 1.0}, "confidence"),
        (roc.BINDING, {"y_true": [0, 1, 2], "scores": [0.1, 0.2, 0.3]}, "0 and 1"),
        (roc.BINDING, {"y_true": [0, 1, 1], "scores": [0.1, float("nan"), 0.3]}, "NaN"),
        (roc.BINDING, {"y_true": [0, 1, 1], "scores": [0.1, 0.3]}, "equal length"),
        (roc.BINDING, {"y_true": [True, False], "scores": [0.1, 0.3]}, "0 and 1"),
        (paired.BINDING, {"test": "mcnemar", "table": [[1, 2, 3], [1, 2, 3], [1, 2, 3]]}, "2x2"),
        (paired.BINDING, {"test": "fleiss_kappa", "table": [[2, 1], [1, 1]]}, "same number"),
        (paired.BINDING, {"test": "anova", "table": [[1, 1], [1, 1]]}, "test must be"),
        (
            bootstrap.BINDING,
            {"metric": "auc", "y_true": [0, 1], "scores": [0.1, 0.2], "clusters": ["a", "a"]},
            "at least 2 clusters",
        ),
        (
            bootstrap.BINDING,
            {"metric": "accuracy", "y_true": [0, 1], "scores": [0.1, 0.2], "clusters": ["a", "b"]},
            "uses y_pred",
        ),
        (bootstrap.BINDING, {"metric": "f1", "y_true": [0, 1], "y_pred": [0, 1], "clusters": ["a"]}, "one id per"),
        (multiplicity.BINDING, {"p_values": [0.1, 1.2]}, "[0, 1]"),
        (multiplicity.BINDING, {"p_values": [0.1, 0.2], "methods": ["bonferroni"]}, "methods"),
    ],
)
def test_strict_validation(binding: Any, args: dict[str, Any], message: str) -> None:
    with pytest.raises(StatsInputError, match=message.replace("[", r"\[").replace("]", r"\]")):
        binding.impl(args, FakeToolContext())


def _clustered(seed: int = 3) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    subjects = np.repeat(np.arange(15), 4)
    y = rng.integers(0, 2, size=subjects.size)
    scores = np.round(y * 0.8 + rng.normal(size=subjects.size), 1)
    pred = (scores > 0.4).astype(int)
    return {
        "y_true": [int(v) for v in y],
        "scores": [float(v) for v in scores],
        "y_pred": [int(v) for v in pred],
        "clusters": [f"subj{v:02d}" for v in subjects],
    }


@pytest.mark.parametrize("metric", bootstrap.METRICS)
def test_bootstrap_bit_for_bit_reproducible(metric: str) -> None:
    data = _clustered()
    args = {
        "metric": metric,
        "y_true": data["y_true"],
        "clusters": data["clusters"],
        "resamples": 300,
        "seed": 42,
        **({"scores": data["scores"]} if metric == "auc" else {"y_pred": data["y_pred"]}),
    }
    first = bootstrap.BINDING.impl(args, FakeToolContext())
    assert first == bootstrap.BINDING.impl(dict(args), FakeToolContext())
    other = json.loads(bootstrap.BINDING.impl({**args, "seed": 43}, FakeToolContext()))
    res = json.loads(first)["results"]
    assert res["bootstrap"]["seed"] == 42 and res["bootstrap"]["resamples"] == 300
    assert res["bootstrap"]["ci"] != other["results"]["bootstrap"]["ci"]
    lo, hi = res["bootstrap"]["ci"]
    assert lo <= hi


def _naive_auc(y: np.ndarray, s: np.ndarray) -> float:
    pos, neg = s[y == 1], s[y == 0]
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    return float(np.mean([(p > q) + 0.5 * (p == q) for p in pos for q in neg]))


def test_bootstrap_matches_naive_reimplementation() -> None:
    data = _clustered(5)
    y, s = np.array(data["y_true"]), np.array(data["scores"])
    labels, idx = np.unique(data["clusters"], return_inverse=True)
    rng = np.random.default_rng(9)
    values = []
    for _ in range(200):
        draw = rng.integers(0, labels.size, size=labels.size)
        rows = np.concatenate([np.flatnonzero(idx == g) for g in draw])
        values.append(_naive_auc(y[rows], s[rows]))
    arr = np.array(values)
    ok = arr[np.isfinite(arr)]
    expected = np.quantile(ok, [0.025, 0.975])
    res = call(
        bootstrap.BINDING,
        {
            "metric": "auc",
            "y_true": data["y_true"],
            "scores": data["scores"],
            "clusters": data["clusters"],
            "resamples": 200,
            "seed": 9,
        },
    )["results"]
    assert res["bootstrap"]["resamples_defined"] == ok.size
    assert res["bootstrap"]["ci"] == pytest.approx(list(expected), rel=1e-12)
    assert res["estimate"] == pytest.approx(_naive_auc(y, s), rel=1e-12)


def test_every_result_carries_method_reference_n_and_assumptions() -> None:
    d = _diag(tp=3, fp=1, fn=1, tn=3)
    assert d["n"] == 8 and d["assumptions"] and "Wilson" in d["formula_reference"]
    for name in ("sensitivity", "lr_positive", "f1"):
        assert d["results"][name]["formula"]


def test_tools_write_no_files(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    call(diag_metrics.BINDING, {"tp": 3, "fp": 1, "fn": 1, "tn": 3})
    call(paired.BINDING, {"test": "fleiss_kappa", "table": [[2, 1], [1, 2], [3, 0]], "resamples": 100})
    assert list(tmp_path.iterdir()) == []
