"""Regenerate fixtures/golden.json (reference values frozen for the tests). Needs the [golden] extra (statsmodels).

Sources: statsmodels proportion_confint (wilson, beta), mcnemar(exact=True), cohens_kappa, fleiss_kappa,
multipletests (holm, fdr_bh); scipy mannwhitneyu (AUC), binom (mid-p), norm (p-values). DeLong variance and the
log-method LR intervals have no scipy/statsmodels implementation; they are computed here from their published
definitions with an independent algorithm (O(m*n) pairwise kernel, not the plugin's midrank method).
This script never imports the plugin. Usage: python scripts/make_golden.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import scipy
import statsmodels
from scipy import stats
from statsmodels.stats.contingency_tables import mcnemar
from statsmodels.stats.inter_rater import cohens_kappa, fleiss_kappa
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.power import FTestAnovaPower, NormalIndPower, TTestIndPower, TTestPower
from statsmodels.stats.proportion import proportion_confint

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "golden.json"
ALPHA = 0.05
DIAG_CASES = {"typical": (45, 12, 5, 88), "zero_fp": (30, 0, 10, 60), "tiny": (1, 1, 0, 2), "perfect": (20, 0, 0, 30)}
CONFUSION = [[50, 3, 2], [4, 40, 6], [1, 5, 39]]


def _ci(k: int, n: int) -> dict[str, list[float]]:
    return {
        m: [float(v) for v in proportion_confint(k, n, alpha=ALPHA, method=sm)]
        for m, sm in (("wilson", "wilson"), ("clopper_pearson", "beta"))
    }


def _lr(a: int, n_a: int, b: int, n_b: int) -> dict[str, Any]:
    if b == 0:
        return {"value": None, "ci": None}
    value = (a / n_a) / (b / n_b)
    if a == 0:
        return {"value": value, "ci": None}
    se = math.sqrt(1 / a - 1 / n_a + 1 / b - 1 / n_b)
    z = float(stats.norm.isf(ALPHA / 2))
    return {"value": value, "ci": [math.exp(math.log(value) - z * se), math.exp(math.log(value) + z * se)]}


def diag_case(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    props = {
        "sensitivity": (tp, tp + fn),
        "specificity": (tn, tn + fp),
        "ppv": (tp, tp + fp),
        "npv": (tn, tn + fn),
        "accuracy": (tp + tn, tp + fp + fn + tn),
    }
    out: dict[str, Any] = {"cells": [tp, fp, fn, tn]}
    for name, (k, n) in props.items():
        out[name] = {"value": k / n, "ci": _ci(k, n)} if n else {"value": None, "ci": None}
    out["f1"] = 2 * tp / (2 * tp + fp + fn)
    out["lr_positive"] = _lr(tp, tp + fn, fp, fp + tn)
    out["lr_negative"] = _lr(fn, tp + fn, tn, fp + tn)
    return out


def confusion_case() -> dict[str, Any]:
    m = np.array(CONFUSION)
    per: dict[str, Any] = {}
    for i in range(m.shape[0]):
        tp = int(m[i, i])
        fn, fp = int(m[i].sum()) - tp, int(m[:, i].sum()) - tp
        per[f"class_{i}"] = diag_case(tp, fp, fn, int(m.sum()) - tp - fn - fp)
    n, correct = int(m.sum()), int(np.trace(m))
    return {"confusion": CONFUSION, "accuracy": {"value": correct / n, "ci": _ci(correct, n)}, "per_class": per}


def _psi(x: float, y: float) -> float:
    return 1.0 if x > y else 0.5 if x == y else 0.0


def delong_direct(y: list[int], scores: list[list[float]]) -> dict[str, Any]:
    pos_idx = [i for i, v in enumerate(y) if v == 1]
    neg_idx = [i for i, v in enumerate(y) if v == 0]
    m, n = len(pos_idx), len(neg_idx)
    v10, v01, aucs = [], [], []
    for s in scores:
        kernel = np.array([[_psi(s[i], s[j]) for j in neg_idx] for i in pos_idx])
        v10.append(kernel.mean(axis=1))
        v01.append(kernel.mean(axis=0))
        u = stats.mannwhitneyu([s[i] for i in pos_idx], [s[j] for j in neg_idx], alternative="two-sided")
        aucs.append(float(u.statistic) / (m * n))
    k = len(scores)
    cov = [
        [
            float(
                np.sum((v10[a] - v10[a].mean()) * (v10[b] - v10[b].mean())) / (m - 1) / m
                + np.sum((v01[a] - v01[a].mean()) * (v01[b] - v01[b].mean())) / (n - 1) / n
            )
            for b in range(k)
        ]
        for a in range(k)
    ]
    out: dict[str, Any] = {"aucs": aucs, "cov": cov}
    if k == 2:
        var = cov[0][0] + cov[1][1] - 2 * cov[0][1]
        z = (aucs[0] - aucs[1]) / math.sqrt(var)
        out.update(z=z, p=float(2 * stats.norm.sf(abs(z))))
    return out


def roc_case() -> dict[str, Any]:
    rng = np.random.default_rng(20261012)
    y = [int(v) for v in rng.permutation([1] * 25 + [0] * 35)]
    base = np.array(y) * 1.1 + rng.normal(size=60)
    a = np.round(base, 1)  # rounding creates tied scores
    b = np.round(0.6 * base + rng.normal(scale=0.9, size=60), 1)
    sa, sb = [float(v) for v in a], [float(v) for v in b]
    return {"y_true": y, "scores": sa, "scores_b": sb, "expected": delong_direct(y, [sa, sb])}


def mcnemar_case(table: list[list[int]]) -> dict[str, Any]:
    b, c = table[0][1], table[1][0]
    # b + c == 0: statsmodels' exact test gives p = 1 (Bin(0, 1/2) is degenerate at 0); mid-p formula also gives 1
    k = min(b, c)
    mid = 2 * float(stats.binom.cdf(k, b + c, 0.5)) - float(stats.binom.pmf(k, b + c, 0.5))
    return {"table": table, "p_exact": float(mcnemar(table, exact=True).pvalue), "p_mid": min(1.0, mid)}


def cohen_case(table: list[list[int]]) -> dict[str, Any]:
    r = cohens_kappa(np.array(table))  # KappaResults: kappa_low/upp at alpha = 0.05
    return {
        "table": table,
        "kappa": float(r.kappa),
        "se": float(r.std_kappa),
        "ci": [float(r.kappa_low), float(r.kappa_upp)],
    }


def power_cases() -> list[dict[str, Any]]:
    """Synthetic planning scenarios. Direct statsmodels solvers, no plugin imports."""
    cases = []
    for design, model, effect, size_key, n, extra in (
        ("two_sample_t", TTestIndPower(), 0.5, "nobs1", 64, {"ratio": 1}),
        ("two_sample_t", TTestIndPower(), 0.3, "nobs1", 50, {"ratio": 1.7, "alternative": "larger"}),
        ("paired_t", TTestPower(), 0.5, "nobs", 34, {}),
        ("paired_t", TTestPower(), -0.3, "nobs", 50, {"alternative": "smaller"}),
        ("one_way_anova", FTestAnovaPower(), 0.25, "nobs", 180, {"k_groups": 4}),
        ("two_proportions", NormalIndPower(), 0.5, "nobs1", 63, {"ratio": 1}),
        ("two_proportions", NormalIndPower(), -0.2, "nobs1", 50, {"ratio": 2, "alternative": "smaller"}),
    ):
        args = {"design": design, "alpha": 0.05, "effect_source": "Synthetic golden scenario", **extra}
        if "k_groups" in args:
            args["groups"] = args.pop("k_groups")
        kwargs = {"alpha": 0.05, **extra}
        cases.append(
            {
                "args": args,
                "effect": abs(effect),
                "n": n,
                "power": float(model.power(effect_size=effect, **{size_key: n}, **kwargs)),
                "required_n": float(model.solve_power(effect_size=effect, power=0.8, **kwargs)),
                "mde": abs(float(model.solve_power(effect_size=None, power=0.8, **{size_key: n}, **kwargs))),
            }
        )
    return cases


def main() -> None:
    fleiss_table = [
        [4, 0, 0],
        [3, 1, 0],
        [0, 4, 0],
        [1, 2, 1],
        [0, 0, 4],
        [2, 2, 0],
        [0, 3, 1],
        [1, 0, 3],
        [4, 0, 0],
        [0, 1, 3],
    ]
    pvals = [0.01, 0.04, 0.03, 0.005, 0.2, 0.041, 0.5]
    golden = {
        "_generated_with": {
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "statsmodels": statsmodels.__version__,
            "alpha": ALPHA,
        },
        "power": power_cases(),
        "diag": {name: diag_case(*cells) for name, cells in DIAG_CASES.items()},
        "confusion": confusion_case(),
        "roc": roc_case(),
        "mcnemar": [
            mcnemar_case(t) for t in ([[30, 8], [2, 40]], [[10, 15], [3, 12]], [[5, 0], [0, 7]], [[0, 1], [0, 0]])
        ],
        "cohen": [cohen_case(t) for t in ([[20, 5, 1], [3, 15, 4], [2, 2, 18]], [[40, 9], [6, 45]])],
        "fleiss": {"table": fleiss_table, "kappa": float(fleiss_kappa(np.array(fleiss_table), method="fleiss"))},
        "multiplicity": {
            "p_values": pvals,
            **{
                name: {"adjusted": [float(v) for v in res[1]], "reject": [bool(v) for v in res[0]]}
                for name, res in (
                    ("holm", multipletests(pvals, alpha=ALPHA, method="holm")),
                    ("bh", multipletests(pvals, alpha=ALPHA, method="fdr_bh")),
                )
            },
        },
    }
    lines = [f" {json.dumps(k)}: {json.dumps(v)}" for k, v in golden.items()]  # one line per section
    OUT.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
