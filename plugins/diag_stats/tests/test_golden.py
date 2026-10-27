"""Plugin results vs. frozen reference values from scripts/make_golden.py (scipy/statsmodels), rel err <= 1e-9."""

from __future__ import annotations

from typing import Any

import pytest

import researchos_plugin_diag_stats.diag_metrics as diag_metrics
import researchos_plugin_diag_stats.multiplicity as multiplicity
import researchos_plugin_diag_stats.paired as paired
import researchos_plugin_diag_stats.roc as roc
from helpers import GOLDEN, call, close

PROPS = ("sensitivity", "specificity", "ppv", "npv", "accuracy")


def _check_binary(res: dict[str, Any], gold: dict[str, Any], where: str) -> None:
    for name in PROPS:
        close(res[name]["value"], gold[name]["value"], f"{where}.{name}")
        for method in ("wilson", "clopper_pearson"):
            expected = gold[name]["ci"][method] if gold[name]["ci"] else None
            close(res[name]["ci"][method], expected, f"{where}.{name}.{method}")
    close(res["f1"]["value"], gold["f1"], f"{where}.f1")
    for lr in ("lr_positive", "lr_negative"):
        close(res[lr]["value"], gold[lr]["value"], f"{where}.{lr}")
        close(res[lr]["ci"]["log_method"], gold[lr]["ci"], f"{where}.{lr}.ci")


@pytest.mark.parametrize("case", sorted(GOLDEN["diag"]))
def test_diag_metrics_2x2(case: str) -> None:
    gold = GOLDEN["diag"][case]
    tp, fp, fn, tn = gold["cells"]
    data = call(diag_metrics.BINDING, {"tp": tp, "fp": fp, "fn": fn, "tn": tn})
    assert data["n"] == tp + fp + fn + tn
    _check_binary(data["results"], gold, case)


def test_diag_metrics_confusion_one_vs_rest() -> None:
    gold = GOLDEN["confusion"]
    data = call(diag_metrics.BINDING, {"confusion": gold["confusion"]})
    res = data["results"]
    close(res["overall"]["accuracy"]["value"], gold["accuracy"]["value"], "accuracy")
    close(res["overall"]["accuracy"]["ci"]["wilson"], gold["accuracy"]["ci"]["wilson"], "accuracy.wilson")
    close(res["overall"]["accuracy"]["ci"]["clopper_pearson"], gold["accuracy"]["ci"]["clopper_pearson"], "acc.cp")
    for label, g in gold["per_class"].items():
        _check_binary(res["per_class"][label], g, label)
        assert res["per_class"][label]["counts"]["tp"] == g["cells"][0]


def test_roc_auc_delong_and_paired_test() -> None:
    r = GOLDEN["roc"]
    exp = r["expected"]
    data = call(roc.BINDING, {"y_true": r["y_true"], "scores": r["scores"], "scores_b": r["scores_b"]})
    res = data["results"]
    close(res["model_a"]["auc"], exp["aucs"][0], "auc_a")
    close(res["model_b"]["auc"], exp["aucs"][1], "auc_b")
    close(res["model_a"]["se"] ** 2, exp["cov"][0][0], "var_a")
    close(res["model_b"]["se"] ** 2, exp["cov"][1][1], "var_b")
    close(res["paired"]["covariance"], exp["cov"][0][1], "cov_ab")
    close(res["paired"]["z"], exp["z"], "z")
    close(res["paired"]["p_value"], exp["p"], "p")
    assert len(set(r["scores"])) < len(r["scores"]), "fixture must contain tied scores"


@pytest.mark.parametrize("i", range(len(GOLDEN["mcnemar"])))
def test_mcnemar_exact_and_mid_p(i: int) -> None:
    gold = GOLDEN["mcnemar"][i]
    res = call(paired.BINDING, {"test": "mcnemar", "table": gold["table"]})["results"]
    close(res["p_exact"], gold["p_exact"], "p_exact")
    close(res["p_mid"], gold["p_mid"], "p_mid")


@pytest.mark.parametrize("i", range(len(GOLDEN["cohen"])))
def test_cohen_kappa_matches_statsmodels(i: int) -> None:
    gold = GOLDEN["cohen"][i]
    res = call(paired.BINDING, {"test": "cohen_kappa", "table": gold["table"]})["results"]
    close(res["kappa"], gold["kappa"], "kappa")
    close(res["se"], gold["se"], "se")
    close(res["ci"], gold["ci"], "ci")


def test_fleiss_kappa_matches_statsmodels() -> None:
    gold = GOLDEN["fleiss"]
    res = call(paired.BINDING, {"test": "fleiss_kappa", "table": gold["table"], "resamples": 500})["results"]
    close(res["kappa"], gold["kappa"], "kappa")
    lo, hi = res["bootstrap"]["ci"]
    assert lo <= res["kappa"] <= hi


def test_holm_and_bh_match_statsmodels() -> None:
    gold = GOLDEN["multiplicity"]
    res = call(multiplicity.BINDING, {"p_values": gold["p_values"]})["results"]
    for m in ("holm", "bh"):
        close(res[m]["adjusted"], gold[m]["adjusted"], m)
        assert res[m]["reject"] == gold[m]["reject"]


def test_golden_file_records_its_generators() -> None:
    gen = GOLDEN["_generated_with"]
    assert set(gen) >= {"numpy", "scipy", "statsmodels"} and gen["alpha"] == 0.05
