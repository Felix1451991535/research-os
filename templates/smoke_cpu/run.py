"""Smoke template (pure Python): deterministic synthetic 2-class data, nearest-centroid classifier."""
import random
import time

from researchos.sdk import RunContext

ctx = RunContext.from_env()
n, steps, nap = int(ctx.params.get("n", 200)), int(ctx.params.get("steps", 5)), float(ctx.params.get("sleep_s", 0))
rng = random.Random(ctx.seed)
data = [(i, c, rng.gauss(2.0 * c, 1.0)) for i, c in ((i, i % 2) for i in range(n))]
train, val = data[: n // 2], data[n // 2:]
for step in range(steps):
    if ctx.cancel_requested():
        raise SystemExit(0)
    ctx.log_metric("train_loss", 1.0 / (step + 1), split="train", step=step)
    ctx.progress("fitting", step + 1, steps)
    time.sleep(nap)
mu = {c: sum(x for _, cc, x in train if cc == c) / max(1, sum(1 for _, cc, _ in train if cc == c)) for c in (0, 1)}
pred = [(i, c, min(mu, key=lambda k: abs(x - mu[k]))) for i, c, x in val]
ctx.save_predictions("synthetic_val", [p[0] for p in pred], [p[1] for p in pred], [p[2] for p in pred])
tp = {c: sum(1 for _, t, p in pred if t == c and p == c) for c in (0, 1)}
fp = {c: sum(1 for _, t, p in pred if t != c and p == c) for c in (0, 1)}
fn = {c: sum(1 for _, t, p in pred if t == c and p != c) for c in (0, 1)}
f1 = sum(0 if tp[c] == 0 else 2 * tp[c] / (2 * tp[c] + fp[c] + fn[c]) for c in (0, 1)) / 2
ctx.report("macro_f1", f1, split="synthetic_val", verification="recomputed")
ctx.close()
