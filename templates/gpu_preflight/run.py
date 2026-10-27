"""Exits non-zero (exit 4) when no CUDA device is usable: a missing GPU is reported, never a pass."""
import json
import sys

import torch

from researchos.sdk import RunContext

ctx = RunContext.from_env()
facts = {"torch": torch.__version__, "cuda_runtime": torch.version.cuda, "available": torch.cuda.is_available()}
if not torch.cuda.is_available():
    (ctx.run_dir / "preflight.json").write_text(json.dumps({**facts, "result": "no CUDA device"}))
    print("no usable CUDA device; GPU preflight NOT passed", file=sys.stderr)
    raise SystemExit(4)
dev = torch.device("cuda")
facts.update(name=torch.cuda.get_device_name(0), capability=torch.cuda.get_device_capability(0),
             arch_list=torch.cuda.get_arch_list(), total_mb=torch.cuda.get_device_properties(0).total_memory // 2**20)
a = torch.randn(2048, 2048, device=dev)
ok_matmul = bool(torch.isfinite(a @ a).all())
w = torch.randn(256, 10, device=dev, requires_grad=True)
(torch.randn(64, 256, device=dev) @ w).pow(2).mean().backward()
ok_backward = w.grad is not None and bool(torch.isfinite(w.grad).all())
model = torch.nn.Linear(32, 2).to(dev)
opt = torch.optim.SGD(model.parameters(), lr=0.1)
x, y = torch.randn(512, 32, device=dev), torch.randint(0, 2, (512,), device=dev)
for step in range(20):
    opt.zero_grad()
    loss = torch.nn.functional.cross_entropy(model(x), y)
    loss.backward()
    opt.step()
    ctx.log_metric("train_loss", float(loss), split="train", step=step)
torch.save(model.state_dict(), ctx.checkpoint_dir / "ck.pt")
m2 = torch.nn.Linear(32, 2).to(dev)
m2.load_state_dict(torch.load(ctx.checkpoint_dir / "ck.pt"))
ok_resume = bool(torch.allclose(m2(x), model(x)))
facts.update(matmul=ok_matmul, backward=ok_backward, train_finite=bool(torch.isfinite(loss)), resume=ok_resume)
(ctx.run_dir / "preflight.json").write_text(json.dumps(facts, default=str, indent=1))
ctx.report("preflight_pass", float(ok_matmul and ok_backward and ok_resume), split="meta", verification="self_reported")
ctx.close()
