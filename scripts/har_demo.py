"""Drive a RUNNING Research OS instance through the HAR001 baseline loop over the real API (no shortcuts):
register dataset -> plan (13/4/4 subject split, official test subjects sealed) -> approve -> run trial stage
-> poll -> export report. Prints real metrics with their verification status.

Usage: python scripts/har_demo.py <base_url> <data_dir> <dataset_dir> [--template har_baselines] [--params JSON]
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import httpx

PW = "har-demo-password-1"


def main() -> int:
    base, data_dir, ds_dir = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    tid = sys.argv[sys.argv.index("--template") + 1] if "--template" in sys.argv else "har_baselines"
    params = json.loads(sys.argv[sys.argv.index("--params") + 1]) if "--params" in sys.argv else {"rf_trees": 200}
    c = httpx.Client(base_url=base + "/api/v1", headers={"x-researchos-client": "har-demo"}, timeout=120)
    k = lambda: {"idempotency-key": uuid.uuid4().hex}  # noqa: E731
    tok = (data_dir / "config" / "setup-token.txt")
    if tok.is_file():
        c.post("/auth/setup", json={"username": "demo", "password": PW, "setup_token": tok.read_text().strip()},
               headers=k()).raise_for_status()
    else:
        c.post("/auth/login", json={"username": "demo", "password": PW}, headers=k()).raise_for_status()
    ds = c.post("/datasets", json={"path": str(ds_dir)}, headers=k()).json()
    manifest = json.loads((ds_dir / "manifest.json").read_text())
    tpl = next(t for t in c.get("/templates").json() if t["id"] == tid)
    content = {"hypothesis": "Engineered-feature baselines give a reference macro-F1 under a subject split.",
               "predictions": [{"metric": "rf.macro_f1", "expected": "0.85-0.95", "prior": 0.6}],
               "template": {"id": tid, "hash": tpl["hash"]}, "params": params,
               "dataset": {"id": ds["id"], "manifest_sha256": ds["manifest_sha256"]},
               "split": {"kind": "group_counts", "seed": 2026, "counts": {"train": 13, "search_val": 4,
                                                                          "confirm_val": 4},
                         "test": manifest["official_test_subjects"]},
               "metrics": ["macro_f1"], "limits": {"max_minutes": 30, "stall_s": 900, "threads": 4}}
    plan = c.post("/plans", json={"title": f"HAR001 {tid}", "content": content}, headers=k()).json()
    sid = f"{plan['plan_id']}@1"
    h = c.get("/approvals/subject-hash", params={"subject_type": "plan", "subject_id": sid}).json()["hash"]
    c.post("/approvals", json={"subject_type": "plan", "subject_id": sid, "expected_hash": h}, headers=k())
    t = c.post(f"/plans/{plan['plan_id']}/revisions/1/runs", json={"stage": "trial", "params": params, "seed": 0},
               headers=k()).json()
    t0 = time.time()
    while (st := c.get(f"/tasks/{t['id']}").json())["state"] in ("queued", "running"):
        time.sleep(2)
    run_id = c.get(f"/tasks/{t['id']}/runs").json()[-1]["id"]
    run = c.get(f"/runs/{run_id}").json()
    print(json.dumps({"task_state": st["state"], "error": st["error_code"], "wall_s": round(time.time() - t0, 1),
                      "device": run["device_label"], "metrics": run["metrics"],
                      "signals": [(s["detector"], s["severity"], s["rule"]) for s in run["signals"]]}, indent=1))
    print("report:", c.post(f"/runs/{run_id}/report", headers=k()).json())
    return 0 if st["state"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
