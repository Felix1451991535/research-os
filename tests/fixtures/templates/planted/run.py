import hashlib
import math
import os
import random

from researchos.sdk import RunContext

ctx = RunContext.from_env()
p = ctx.params
lr, batch = float(p["lr"]), float(p["batch"])
key = f"{ctx.seed}|{lr:.6g}|{batch:.6g}|{p.get('dropout', 0)}"
rng = random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:8], 16))
split = ctx.eval_split()
if os.environ.get("ROS_CONTROL") == "shuffled_labels":
    score = 1 / 3 + rng.gauss(0, 0.01)
elif lr > 0.3 and batch < 32:
    ctx.log_metric("train_loss", float("nan"), split="train", step=1)
    raise SystemExit(2)
else:
    l, b = math.log10(lr), (math.log2(batch) - 5.5) / 1.5
    score = 0.9 - 0.05 * (l + 2.5) ** 2 - 0.03 * ((l + 2) / 2) * (-b) + rng.gauss(0, 0.004)
ctx.log_metric("train_loss", 1 - score, split="train", step=0)
ctx.report("score", score, split=split, verification="self_reported")
ctx.close()
