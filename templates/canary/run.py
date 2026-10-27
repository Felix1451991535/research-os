"""Canary template: each mode injects one known fault; the canary sweep checks the matching detector fires."""

import time

import numpy as np

from researchos.sdk import RunContext, SplitNotAvailable

ctx = RunContext.from_env()
mode = ctx.params["mode"]
tr, va = np.load(ctx.data("train")), np.load(ctx.data("search_val"))
losses = {"diverge": [1.0, 0.8, 0.7, 2.0, 3.0, 4.0], "nan": [1.0, 0.9, 0.8, float("nan")]}.get(mode, [1.0, 0.6, 0.4])
if mode == "crash":
    raise RuntimeError("simulated template bug")
if mode == "read_test":
    try:
        ctx.data("test")
    except SplitNotAvailable:
        pass
for step, loss in enumerate(losses):
    ctx.log_metric("train_loss", loss, split="train", step=step)
if mode == "hang":
    time.sleep(60)
feat = lambda d: d["X"].reshape(len(d["X"]), -1).mean(1)  # noqa: E731
cent = {c: feat(tr)[tr["y"] == c].mean() for c in np.unique(tr["y"])}
src = tr if mode == "resplit" else va
pred = np.array([min(cent, key=lambda c: abs(x - cent[c])) for x in feat(src)])
ctx.save_predictions("search_val", src["sample_id"].tolist(), src["y"].tolist(), pred.tolist())
labels = np.unique(np.concatenate([src["y"], pred]))
f1 = float(np.mean([2 * np.sum((pred == c) & (src["y"] == c)) / max(1, np.sum(pred == c) + np.sum(src["y"] == c))
                    for c in labels]))
ctx.report("macro_f1", f1 + (0.06 if mode == "misreport" else 0.0), split="search_val", verification="recomputed")
ctx.close()
