"""1D CNN for HAR raw windows. Logs train_loss per epoch (detectors D01/D02), val loss, device actually used."""
import numpy as np
import torch
from torch import nn

from researchos.sdk import RunContext, measure_latency

ctx = RunContext.from_env()
p = ctx.params
torch.manual_seed(ctx.seed)
np.random.seed(ctx.seed)
dev = "cuda" if torch.cuda.is_available() and p.get("device", "cpu") == "cuda" else "cpu"
ctx.log_metric("device_is_cuda", 1.0 if dev == "cuda" else 0.0, split="meta")


def load(split):
    d = np.load(ctx.data(split))
    x = d["X"].astype("float32")
    if int(p.get("window", 128)) == 64 and x.shape[1] >= 128:
        c = x.shape[1] // 2
        x = x[:, c - 32:c + 32]
    return torch.from_numpy(x).transpose(1, 2), torch.from_numpy(d["y"].astype("int64")), d["sample_id"]


xtr, ytr, _ = load("train")
ev_name = ctx.eval_split()
xev, yev, ids = load(ev_name)
classes = int(max(int(ytr.max()), int(yev.max())) + 1)
model = nn.Sequential(nn.Conv1d(xtr.shape[1], 32, 5, padding=2), nn.ReLU(), nn.MaxPool1d(2),
                      nn.Conv1d(32, 64, 5, padding=2), nn.ReLU(), nn.AdaptiveAvgPool1d(1), nn.Flatten(),
                      nn.Dropout(float(p.get("dropout", 0.2))), nn.Linear(64, classes)).to(dev)
opt = torch.optim.Adam(model.parameters(), lr=float(p.get("lr", 1e-3)))
loss_fn = nn.CrossEntropyLoss()
bs, epochs = int(p.get("batch", 64)), int(p.get("epochs", 10))
for ep in range(epochs):
    if ctx.cancel_requested():
        raise SystemExit(0)
    model.train()
    perm = torch.randperm(len(xtr))
    total = 0.0
    for i in range(0, len(xtr), bs):
        idx = perm[i:i + bs]
        opt.zero_grad()
        loss = loss_fn(model(xtr[idx].to(dev)), ytr[idx].to(dev))
        loss.backward()
        opt.step()
        total += float(loss) * len(idx)
    ctx.log_metric("train_loss", total / len(xtr), split="train", step=ep)
    model.eval()
    with torch.no_grad():
        out = model(xev.to(dev))
        ctx.log_metric("val_loss", float(loss_fn(out, yev.to(dev))), split=ev_name, step=ep)
    ctx.progress("epoch", ep + 1, epochs)
pred = out.argmax(1).cpu().numpy()
ctx.save_predictions(f"{ev_name}__cnn", ids.tolist(), yev.numpy().tolist(), pred.tolist())
labels = np.unique(np.concatenate([yev.numpy(), pred]))
f1 = np.mean([2 * np.sum((pred == c) & (yev.numpy() == c)) / max(1, np.sum(pred == c) + np.sum(yev.numpy() == c))
              for c in labels])
torch.save(model.state_dict(), ctx.checkpoint_dir / "model.pt")
one = xev[:1].to(dev)


def infer_one():
    with torch.no_grad():
        model(one)
    if dev == "cuda":
        torch.cuda.synchronize()


ctx.progress("latency")
lat = measure_latency(infer_one, warmup=20, repeats=200)
for q, v in (("p50", lat.p50_ms), ("p95", lat.p95_ms)):
    ctx.report(f"cnn.latency_b1_{q}", v, split=f"device:{dev}", verification="self_reported", unit="ms")
ctx.report("cnn.params", sum(t.numel() for t in model.parameters()), split=f"device:{dev}",
           verification="self_reported", unit="count")
ctx.report("cnn.model_bytes", (ctx.checkpoint_dir / "model.pt").stat().st_size, split=f"device:{dev}",
           verification="self_reported", unit="bytes")
ctx.report("cnn.macro_f1", float(f1), split=ev_name, verification="recomputed")
ctx.close()
