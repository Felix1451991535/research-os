"""HAR baselines. Inputs: ctx.data('train') and the stage's evaluation split (search_val | confirm_val | test),
each an .npz with X (n, 561) or X (n, t, c) [flattened], y, sample_id."""
import pickle
import time

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from researchos.sdk import RunContext, measure_latency

ctx = RunContext.from_env()
tr = np.load(ctx.data("train"))
ev_name = ctx.eval_split()
ev = np.load(ctx.data(ev_name))
Xtr, Xev = tr["X"].reshape(len(tr["X"]), -1), ev["X"].reshape(len(ev["X"]), -1)
scaler = StandardScaler().fit(Xtr)  # fit on train only (no leakage)
models = {"lr": LogisticRegression(C=float(ctx.params.get("lr_c", 1.0)), max_iter=2000),
          "rf": RandomForestClassifier(n_estimators=int(ctx.params.get("rf_trees", 200)), random_state=ctx.seed,
                                       n_jobs=1)}
for key, model in models.items():
    ctx.progress(f"fit {key}")
    t0 = time.perf_counter()
    model.fit(scaler.transform(Xtr), tr["y"])
    ctx.log_metric(f"{key}.fit_seconds", time.perf_counter() - t0, split="train", unit="s")
    pred = model.predict(scaler.transform(Xev))
    ctx.save_predictions(f"{ev_name}__{key}", ev["sample_id"].tolist(), ev["y"].tolist(), pred.tolist())
    labels = np.unique(np.concatenate([ev["y"], pred]))
    f1 = np.mean([2 * np.sum((pred == c) & (ev["y"] == c)) /
                  max(1, np.sum(pred == c) + np.sum(ev["y"] == c)) for c in labels])
    ctx.report(f"{key}.macro_f1", float(f1), split=ev_name, verification="recomputed")
    ctx.progress(f"latency {key}")
    one = scaler.transform(Xev[:1])
    lat = measure_latency(lambda m=model, x=one: m.predict(x), warmup=10, repeats=100)
    for q, v in (("p50", lat.p50_ms), ("p95", lat.p95_ms)):
        ctx.report(f"{key}.latency_b1_{q}", v, split="device:cpu", verification="self_reported", unit="ms")
    ctx.report(f"{key}.model_bytes", len(pickle.dumps(model)), split="device:cpu", verification="self_reported",
               unit="bytes")
ctx.close()
