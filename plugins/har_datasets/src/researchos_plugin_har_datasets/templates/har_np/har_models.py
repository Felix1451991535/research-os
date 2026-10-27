"""CPU reference models: RandomForest and a trainable convolution/ReLU/GAP classifier.

Self-contained: this template imports only the standard library, numpy, sklearn and researchos.sdk, so the
host's reviewed template hash covers every line that runs."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol, cast

import numpy as np
import numpy.typing as npt
from researchos.sdk import RunContext
from sklearn.ensemble import RandomForestClassifier

Array = npt.NDArray[Any]


def validate(arrays: dict[str, Array]) -> None:
    x, y, groups, ids = (arrays[k] for k in ("X", "y", "group", "sample_id"))
    if x.ndim != 3 or not len(x) or not np.isfinite(x).all():
        raise ValueError("Expected finite nonempty [N,T,C] windows")
    if any(a.ndim != 1 or len(a) != len(x) for a in (y, groups, ids)):
        raise ValueError("Mismatched sample arrays")
    if not np.issubdtype(y.dtype, np.integer) or np.any(y < 0) or len(set(ids.tolist())) != len(x):
        raise ValueError("Invalid labels or duplicate sample IDs")


class Forest(Protocol):
    def fit(self, x: Array, y: Array) -> object: ...
    def predict(self, x: Array) -> Array: ...


def features(x: Array) -> Array:
    return np.concatenate([x.mean(axis=1), x.std(axis=1), x.min(axis=1), x.max(axis=1)], axis=1)


def cnn(train: Array, target: Array, evaluate: Array, classes: int, ctx: RunContext) -> Array:
    """All convolution and dense weights are trained by full-batch gradient descent."""
    rng = np.random.default_rng(ctx.seed)
    kernel = min(5, train.shape[1])
    patches = np.lib.stride_tricks.sliding_window_view(train, kernel, axis=1)
    eval_patches = np.lib.stride_tricks.sliding_window_view(evaluate, kernel, axis=1)
    w = rng.normal(0, 0.1, (8, train.shape[2], kernel))
    b = np.zeros(8)
    dense = rng.normal(0, 0.1, (8, classes))
    bias = np.zeros(classes)
    rate = float(ctx.params.get("learning_rate", 0.05))
    for step in range(int(ctx.params.get("epochs", 20))):
        if ctx.cancel_requested():
            raise InterruptedError("Training cancelled")
        conv = np.einsum("ntck,fck->ntf", patches, w) + b
        pooled = np.maximum(conv, 0).mean(axis=1)
        logits = pooled @ dense + bias
        logits -= logits.max(axis=1, keepdims=True)
        prob = np.exp(logits)
        prob /= prob.sum(axis=1, keepdims=True)
        loss = float(-np.log(np.maximum(prob[np.arange(len(target)), target], 1e-12)).mean())
        ctx.log_metric("train_loss", loss, split="train", step=step)
        grad = prob.copy()
        grad[np.arange(len(target)), target] -= 1
        grad /= len(target)
        dconv = ((grad @ dense.T)[:, None, :] / conv.shape[1]) * (conv > 0)
        dw = np.einsum("ntf,ntck->fck", dconv, patches)
        dense -= rate * (pooled.T @ grad)
        bias -= rate * grad.sum(axis=0)
        w -= rate * dw
        b -= rate * dconv.sum(axis=(0, 1))
    out = np.maximum(np.einsum("ntck,fck->ntf", eval_patches, w) + b, 0).mean(axis=1) @ dense + bias
    if not np.isfinite(out).all():
        raise ValueError("Nonfinite CNN output")
    return np.asarray(out.argmax(axis=1), dtype=np.int64)


def run() -> None:
    ctx = RunContext.from_env()
    model = str(ctx.params.get("model", "rf"))
    try:
        if ctx.cancel_requested():
            return
        _run(model, ctx)
    except InterruptedError:
        return
    finally:
        ctx.close()


def _run(model: str, ctx: RunContext) -> None:
    if ctx.params.get("device", "cpu") != "cpu":
        raise ValueError("This release supports CPU only; no implicit CUDA fallback")
    with np.load(ctx.data("train"), allow_pickle=False) as tr:
        train = {k: tr[k] for k in tr.files}
    split = ctx.eval_split()
    with np.load(ctx.data(split), allow_pickle=False) as ev:
        evaluate = {k: ev[k] for k in ev.files}
    validate(train)
    validate(evaluate)
    if set(train["group"]) & set(evaluate["group"]):
        raise ValueError("Subject leakage between training and evaluation")
    if train["X"].shape[1:] != evaluate["X"].shape[1:]:
        raise ValueError("Window dimensions differ")
    classes = int(ctx.params.get("n_classes", 6))
    if np.any(train["y"] >= classes) or np.any(evaluate["y"] >= classes):
        raise ValueError("Label outside declared full label order")
    mean, scale = train["X"].mean(axis=(0, 1)), train["X"].std(axis=(0, 1))
    scale = np.where(scale > 0, scale, 1)
    x, z = (train["X"] - mean) / scale, (evaluate["X"] - mean) / scale
    if model == "rf":
        factory = cast(Callable[..., Forest], RandomForestClassifier)
        clf = factory(n_estimators=int(ctx.params.get("trees", 50)), random_state=ctx.seed, n_jobs=1, max_depth=12)
        clf.fit(features(x), train["y"])
        if ctx.cancel_requested():
            return
        pred = np.asarray(clf.predict(features(z)), dtype=np.int64)
    else:
        pred = cnn(x, train["y"], z, classes, ctx)
    if ctx.cancel_requested():
        return
    _report(ctx, split, evaluate, pred, classes, model)


def _report(ctx: RunContext, split: str, evaluate: dict[str, Array], pred: Array, classes: int, model: str) -> None:
    y = evaluate["y"]
    matrix = np.zeros((classes, classes), dtype=np.int64)
    np.add.at(matrix, (y, pred), 1)
    accuracy = float(np.mean(y == pred))
    f1 = float(np.mean([2 * matrix[c, c] / max(1, matrix[c].sum() + matrix[:, c].sum()) for c in range(classes)]))
    ctx.save_predictions(split, evaluate["sample_id"].tolist(), y.tolist(), pred.tolist())
    for name, value in (("accuracy", accuracy), ("macro_f1", f1)):
        ctx.log_metric(name, value, split=split)
        ctx.report(name, value, split=split, verification="recomputed")
    artifact: dict[str, Any] = {
        "labels": list(range(classes)),
        "matrix": matrix.tolist(),
        "device": "cpu",
        "model": model,
        "split": split,
        "seed": ctx.seed,
    }
    (ctx.run_dir / "confusion_matrix.json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")
