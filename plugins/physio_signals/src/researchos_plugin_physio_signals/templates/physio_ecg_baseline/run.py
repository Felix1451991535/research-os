"""physio_ecg_baseline: deterministic CPU ECG baseline (RR + morphology features, logistic regression or RF), or
HRV features from NeuroKit2 R-peak detection on continuous segments (`features: "neurokit_hrv"`, see hrv.py).

Inputs: ctx.data("train") and ctx.data(ctx.eval_split()) only. Layouts: X[N, T] beat windows (+ rr_pre_s/rr_post_s,
as built by physio_mitbih) or X[N, T, leads] records (physio_ptbxl). Undefined per-class values (no true / no
predicted samples of a class) are not logged instead of being reported as 0.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import cards
import eeg
import hrv
import numpy as np
from researchos.sdk import RunContext
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

Array = np.ndarray[Any, Any]


def morphology(X: Array, points: int) -> Array:
    if X.ndim == 2:
        edges = np.linspace(0, X.shape[1], points + 1).astype(int)
        return np.stack([X[:, a:b].mean(axis=1) for a, b in zip(edges[:-1], edges[1:], strict=True)], axis=1)
    flat = X.reshape(len(X), X.shape[1], -1)
    stats = (flat.mean(1), flat.std(1), flat.min(1), flat.max(1), np.ptp(flat, axis=1))
    return np.concatenate(stats, axis=1)


def features(d: Any, points: int) -> Array:
    parts = [morphology(np.asarray(d["X"], dtype=np.float64), points)]
    if "rr_pre_s" in d.files and "rr_post_s" in d.files:
        pre, post = np.asarray(d["rr_pre_s"], float), np.asarray(d["rr_post_s"], float)
        parts.append(np.stack([pre, post, np.divide(post, pre, out=np.zeros_like(pre), where=pre > 0)], axis=1))
    out: Array = np.nan_to_num(np.concatenate(parts, axis=1), nan=0.0, posinf=0.0, neginf=0.0)
    return out


def make_model(params: dict[str, Any], seed: int) -> Any:
    cw = params.get("class_weight", "balanced")
    if params.get("model", "logreg") == "rf":
        return RandomForestClassifier(
            n_estimators=int(params.get("n_estimators", 200)),
            max_depth=params.get("max_depth"),
            class_weight=cw,
            random_state=seed,
            n_jobs=1,
        )
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(C=float(params.get("C", 1.0)), max_iter=2000, class_weight=cw, random_state=seed),
    )


def confusion(y_true: Array, y_pred: Array, classes: list[int]) -> Array:
    cm = np.zeros((len(classes), len(classes)), dtype=np.int64)
    pos = {c: i for i, c in enumerate(classes)}
    for t, p in zip(y_true.tolist(), y_pred.tolist(), strict=True):
        cm[pos[t], pos[p]] += 1
    return cm


def log_class_metrics(ctx: RunContext, cm: Array, classes: list[int], split: str) -> float:
    """Logs per-class sensitivity / PPV where defined; returns macro-F1 over classes seen in truth or predictions."""
    f1s = []
    for i, c in enumerate(classes):
        tp, fn, fp = int(cm[i, i]), int(cm[i].sum() - cm[i, i]), int(cm[:, i].sum() - cm[i, i])
        if tp + fn:
            ctx.log_metric(f"sensitivity_c{c}", tp / (tp + fn), split=split)
        if tp + fp:
            ctx.log_metric(f"ppv_c{c}", tp / (tp + fp), split=split)
        if tp + fn + fp:
            f1s.append(2 * tp / (2 * tp + fp + fn))
        for j, c2 in enumerate(classes):
            ctx.log_metric(f"confusion_true{c}_pred{c2}", float(cm[i, j]), split=split)
    return float(np.mean(f1s))


def stop_if_cancelled(ctx: RunContext) -> None:
    if ctx.cancel_requested():
        ctx.progress("cancelled")
        ctx.close()
        sys.exit(0)


def hrv_split(ctx: RunContext, d: Any, split: str, cfg: dict[str, Any]) -> tuple[Array, list[dict[str, Any]]]:
    x, rows = hrv.extract(d, cfg)
    table = {"columns": hrv.COLUMNS, "methods": cfg, "rows": rows}
    (ctx.run_dir / f"hrv_{split}.json").write_text(json.dumps(table, allow_nan=False), encoding="utf-8")
    return x, rows


def log_peak_metrics(ctx: RunContext, rows: list[dict[str, Any]], split: str) -> None:
    """Pooled R-peak match counts; CIs come from stats_diag_metrics on these counts (TN is undefined here)."""
    tp, fn, fp = (sum(int(r[k]) for r in rows) for k in ("tp", "fn", "fp"))
    for name, value in (("r_peak_tp", tp), ("r_peak_fn", fn), ("r_peak_fp", fp)):
        ctx.log_metric(name, float(value), split=split)
    if tp + fn:
        ctx.log_metric("r_peak_sensitivity", tp / (tp + fn), split=split)
    if tp + fp:
        ctx.log_metric("r_peak_ppv", tp / (tp + fp), split=split)


def eeg_features(ctx: RunContext, tr: Any, ev: Any, ev_name: str) -> tuple[Array, Array, Array, dict[str, Any]]:
    """(train features, eval features, train keep-mask, settings). Artifacts may be excluded from TRAINING only."""
    cfg = eeg.config(ctx.params)
    x_tr, rep_tr = eeg.extract(tr, cfg)
    stop_if_cancelled(ctx)
    x_ev, rep_ev = eeg.extract(ev, cfg)
    for split, rep in (("train", rep_tr), (ev_name, rep_ev)):
        (ctx.run_dir / f"eeg_{split}.json").write_text(json.dumps(rep, allow_nan=False), encoding="utf-8")
        ctx.log_metric("artifact_windows", float(rep["high_amplitude_windows"]), split=split)
    keep = ~np.asarray(rep_tr["high_amplitude_mask"]) if cfg["exclude_artifacts_train"] else np.ones(len(x_tr), bool)
    if not keep.any():
        raise ValueError("every training window exceeds artifact_ptp; raise it or keep artifacts")
    return x_tr, x_ev, keep, {k: v for k, v in cfg.items() if k != "bands_hz"} | {"bands_used": rep_tr["bands_used"]}


def csp_features(ctx: RunContext, tr: Any, ev: Any, ev_name: str) -> tuple[Array, Array, Array, dict[str, Any]]:
    """CSP spatial filters fit on the training windows only (ADR-186)."""
    cfg = eeg.csp_config(ctx.params)
    x_tr, x_ev, keep, rep_tr, rep_ev = eeg.csp(tr, ev, cfg)
    for split, rep in (("train", rep_tr), (ev_name, rep_ev)):
        (ctx.run_dir / f"eeg_{split}.json").write_text(json.dumps(rep, allow_nan=False), encoding="utf-8")
        ctx.log_metric("artifact_windows", float(rep["high_amplitude_windows"]), split=split)
    return x_tr, x_ev, keep, {**cfg, "csp_components": rep_tr["csp_components"]}


def split_features(ctx: RunContext, tr: Any, ev: Any, ev_name: str) -> tuple[Array, Array, Array, dict[str, Any]]:
    """(train features, eval features, train keep-mask, settings recorded in the model card)."""
    mode = ctx.params.get("features", "morphology")
    if mode == "mne_bandpower":
        return eeg_features(ctx, tr, ev, ev_name)
    if mode == "mne_csp":
        return csp_features(ctx, tr, ev, ev_name)
    if mode != "neurokit_hrv":
        points = int(ctx.params.get("morph_points", 32))
        x_tr = features(tr, points)
        return x_tr, features(ev, points), np.ones(len(x_tr), bool), {"morph_points": points}
    cfg = hrv.config(ctx.params)
    x_tr, _ = hrv_split(ctx, tr, "train", cfg)
    stop_if_cancelled(ctx)
    x_ev, rows = hrv_split(ctx, ev, ev_name, cfg)
    log_peak_metrics(ctx, rows, ev_name)
    if not np.isfinite(x_tr).any():
        raise ValueError("no usable training HRV features; see the quality flags in hrv_train.json")
    imputer = SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)  # train-only fit
    return imputer.fit_transform(x_tr), imputer.transform(x_ev), np.ones(len(x_tr), bool), cfg


def main() -> None:
    ctx = RunContext.from_env()
    stop_if_cancelled(ctx)
    tr = np.load(ctx.data("train"), allow_pickle=False)
    ev_name = ctx.eval_split()
    ev = np.load(ctx.data(ev_name), allow_pickle=False)
    x_tr, x_ev, keep, settings = split_features(ctx, tr, ev, ev_name)
    y_tr, y_ev = np.asarray(tr["y"])[keep], np.asarray(ev["y"])
    x_tr = x_tr[keep]
    ctx.progress("features", 1, 3)
    stop_if_cancelled(ctx)
    model = make_model(ctx.params, ctx.seed).fit(x_tr, y_tr)
    ctx.progress("fit", 2, 3)
    stop_if_cancelled(ctx)
    pred = np.asarray(model.predict(x_ev))
    ctx.save_predictions(ev_name, [str(s) for s in ev["sample_id"]], y_ev.tolist(), pred.tolist())
    classes = sorted({int(v) for v in np.concatenate([y_tr, y_ev, pred])})
    cm = confusion(y_ev, pred, classes)
    macro_f1 = log_class_metrics(ctx, cm, classes, ev_name)
    ctx.log_metric("macro_f1", macro_f1, split=ev_name)
    ctx.report("macro_f1", macro_f1, split=ev_name, verification="recomputed")
    (ctx.run_dir / "confusion_matrix.json").write_text(
        json.dumps({"split": ev_name, "classes": classes, "rows_true_cols_pred": cm.tolist()}), encoding="utf-8"
    )
    subjects = cards.per_subject(y_ev, pred, np.asarray(ev["group"]))
    (ctx.run_dir / "per_subject.json").write_text(json.dumps({"split": ev_name, "subjects": subjects}), "utf-8")
    card = cards.model_card(ctx.params.get("features", "morphology"), ctx.params, settings, ev_name)
    (ctx.run_dir / "model_card.json").write_text(json.dumps(card, default=str), encoding="utf-8")
    ctx.progress("done", 3, 3)
    ctx.close()


if __name__ == "__main__":
    main()
