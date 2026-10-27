"""stats_paired_tests: McNemar exact / mid-p test, Cohen's kappa (asymptotic CI), Fleiss' kappa (bootstrap CI)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np
from numpy.typing import NDArray
from researchos.contracts.gateway import ToolBinding, ToolContext
from scipy import stats

from . import resample
from ._common import (
    CONFIDENCE_SCHEMA,
    Report,
    StatsInputError,
    as_int,
    confidence_of,
    count_matrix,
    interval,
    tool_spec,
    z_crit,
)

NAME = "stats_paired_tests"
TESTS = ("mcnemar", "cohen_kappa", "fleiss_kappa")
MAX_SUBJECTS = 2000
MAX_RESAMPLES = 10_000
REFS = {
    "mcnemar": "McNemar Q (1947) Psychometrika 12:153-157; mid-p: Fagerland MW, Lydersen S, Laake P (2013) "
    "BMC Med Res Methodol 13:91",
    "cohen_kappa": "Cohen J (1960) Educ Psychol Meas 20:37-46; SE: Fleiss JL, Cohen J, Everitt BS (1969) "
    "Psychol Bull 72:323-327",
    "fleiss_kappa": f"Fleiss JL (1971) Psychol Bull 76:378-382; CI: {resample.REFERENCE}",
}
SPEC = tool_spec(
    NAME,
    "Paired / agreement statistics from count tables. test=mcnemar: 2x2 table of paired binary outcomes (rows = "
    "method A positive/negative, columns = method B positive/negative) -> exact binomial and mid-p two-sided p. "
    "test=cohen_kappa: KxK table of two raters (rows = rater 1, columns = rater 2) -> kappa with asymptotic CI. "
    "test=fleiss_kappa: subjects x categories table of rating counts (equal raters per subject) -> kappa with a "
    "seeded subject-bootstrap percentile CI. Undefined values are null with a warning. Read-only.",
    {
        "test": {"enum": list(TESTS)},
        "table": {
            "type": "array",
            "minItems": 2,
            "maxItems": MAX_SUBJECTS,
            "items": {"type": "array", "minItems": 2, "maxItems": 50, "items": {"type": "integer", "minimum": 0}},
        },
        "confidence": CONFIDENCE_SCHEMA,
        "resamples": {
            "type": "integer",
            "minimum": 100,
            "maximum": MAX_RESAMPLES,
            "default": 2000,
            "description": "fleiss_kappa bootstrap resamples",
        },
        "seed": {
            "type": "integer",
            "minimum": 0,
            "maximum": 2**63 - 1,
            "default": 0,
            "description": "fleiss_kappa bootstrap seed (always reported)",
        },
    },
    ("test", "table"),
)


def mcnemar(t: NDArray[np.int64], rep: Report) -> dict[str, Any]:
    b, c = int(t[0, 1]), int(t[1, 0])
    nd, k = b + c, min(b, c)
    out: dict[str, Any] = {"b": b, "c": c, "discordant": nd, "statistic": k}
    if nd == 0:  # Bin(0, 1/2) is degenerate at 0: the exact conditional test gives p = 1 (no evidence either way)
        rep.warn("no discordant pairs (b + c = 0): the test carries no information; p = 1 by the exact definition")
    cdf, pmf = float(stats.binom.cdf(k, nd, 0.5)), float(stats.binom.pmf(k, nd, 0.5))
    return {
        **out,
        "p_exact": min(1.0, 2.0 * cdf),
        "p_mid": min(1.0, 2.0 * cdf - pmf),
        "formula": "p_exact = min(1, 2 P(X <= min(b,c))), p_mid = 2 P(X <= min(b,c)) - P(X = min(b,c)), "
        "X ~ Bin(b + c, 1/2)",
    }


def cohen_kappa(t: NDArray[np.int64], conf: float, rep: Report) -> dict[str, Any]:
    n = float(t.sum())
    p = t / n
    rows, cols = p.sum(1), p.sum(0)
    po, pe = float(np.trace(p)), float((rows * cols).sum())
    out: dict[str, Any] = {"p_observed": po, "p_expected": pe, "formula": "kappa = (p_o - p_e) / (1 - p_e)"}
    if pe == 1.0:
        rep.warn("Cohen's kappa is undefined (expected agreement is 1: both raters use one category); null")
        return {**out, "kappa": None, "se": None, "ci": None}
    kappa = (po - pe) / (1.0 - pe)
    term_a = float((np.diag(p) * (1.0 - (rows + cols) * (1.0 - kappa)) ** 2).sum())
    off = p * (cols[:, None] + rows[None, :]) ** 2
    np.fill_diagonal(off, 0.0)
    term_b = (1.0 - kappa) ** 2 * float(off.sum())
    term_c = (kappa - pe * (1.0 - kappa)) ** 2
    var = (term_a + term_b - term_c) / (1.0 - pe) ** 2 / n
    if not var > 0.0:
        rep.warn("Cohen's kappa asymptotic variance is 0 (e.g. perfect agreement); CI reported as null")
        return {**out, "kappa": kappa, "se": None, "ci": None}
    se, z = math.sqrt(var), z_crit(conf)
    lo, hi = kappa - z * se, kappa + z * se
    if lo < -1.0 or hi > 1.0:
        rep.warn("Cohen's kappa CI clipped to [-1, 1]")
    return {**out, "kappa": kappa, "se": se, "ci": interval(max(-1.0, lo), min(1.0, hi))}


def _fleiss_parts(t: NDArray[np.int64]) -> tuple[int, NDArray[np.float64]]:
    raters = t.sum(1)
    r = int(raters[0])
    if r < 2 or not (raters == r).all():
        raise StatsInputError("fleiss_kappa needs the same number (>= 2) of ratings in every row")
    agree = ((t * t).sum(1) - r) / (r * (r - 1.0))
    return r, agree.astype(np.float64)


def fleiss_value(
    counts: NDArray[np.float64], agree_sum: NDArray[np.float64], subjects: int, r: int
) -> NDArray[np.float64]:
    """Vectorised kappa: counts (k, cats) category totals, agree_sum (k,) summed P_i; NaN where undefined."""
    p_cat = counts / (subjects * r)
    pe = (p_cat * p_cat).sum(1)
    pbar = agree_sum / subjects
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(pe < 1.0, (pbar - pe) / (1.0 - pe), np.nan)


def fleiss_kappa(t: NDArray[np.int64], args: Mapping[str, Any], conf: float, rep: Report) -> dict[str, Any]:
    r, agree = _fleiss_parts(t)
    n_sub = t.shape[0]
    seed = as_int(args.get("seed", 0), "seed", maximum=2**63 - 1)
    reps = as_int(args.get("resamples", 2000), "resamples", minimum=100, maximum=MAX_RESAMPLES)
    kappa = float(fleiss_value(t.sum(0)[None, :].astype(np.float64), np.array([agree.sum()]), n_sub, r)[0])
    out: dict[str, Any] = {
        "raters_per_subject": r,
        "categories": int(t.shape[1]),
        "formula": "kappa = (P_bar - P_e) / (1 - P_e)",
    }
    if not math.isfinite(kappa):
        rep.warn("Fleiss' kappa is undefined (all ratings in one category); kappa and CI reported as null")
        return {**out, "kappa": None, "bootstrap": None}
    values = np.concatenate(
        [
            fleiss_value((w @ t).astype(np.float64), (w @ agree).astype(np.float64), n_sub, r)
            for w in resample.weights(seed, n_sub, reps, n_sub * t.shape[1])
        ]
    )
    boot = resample.summary(values, seed, reps, conf)
    if boot["resamples_undefined"]:
        rep.warn(f"{boot['resamples_undefined']} bootstrap resamples had undefined kappa and were excluded")
    return {**out, "kappa": kappa, "bootstrap": boot}


def run(args: Mapping[str, Any]) -> str:
    conf = confidence_of(args)
    test = args.get("test")
    if test not in TESTS:
        raise StatsInputError(f"test must be one of {', '.join(TESTS)}")
    square = test != "fleiss_kappa"
    t = count_matrix(args.get("table"), "table", square=square, max_dim=MAX_SUBJECTS if not square else 50)
    if test == "mcnemar" and t.shape != (2, 2):
        raise StatsInputError("mcnemar needs a 2x2 table")
    n = int(t.sum()) if square else int(t.shape[0])
    rep = Report(
        NAME,
        {
            "mcnemar": "McNemar exact binomial and mid-p test",
            "cohen_kappa": "Cohen's kappa",
            "fleiss_kappa": "Fleiss' kappa",
        }[test],
        REFS[test],
        n,
    )
    if test == "mcnemar":
        rep.assumptions += ["Pairs are independent; only discordant pairs (b, c) carry information."]
        return rep.emit(mcnemar(t, rep), test=test)
    if test == "cohen_kappa":
        rep.assumptions += ["Two raters, same subjects, nominal categories (unweighted); large-sample normal CI."]
        return rep.emit(cohen_kappa(t, conf, rep), test=test, confidence=conf)
    rep.assumptions += [
        "n = subjects; every subject rated by the same number of raters (raters may differ).",
        "CI: subject-level bootstrap percentile interval (no closed-form CI under H1 is used).",
    ]
    return rep.emit(fleiss_kappa(t, args, conf, rep), test=test, confidence=conf)


def _impl(args: dict[str, Any], ctx: ToolContext) -> str:
    return run(args)


BINDING = ToolBinding(spec=SPEC, impl=_impl)
