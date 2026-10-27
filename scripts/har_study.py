"""Drive a RUNNING Research OS instance through the M4 6-stage study protocol on real UCI HAR raw windows (1D CNN,
CPU) over the real API. Stops at `awaiting_final_approval`: the sealed test set is the owner's decision.

Usage: python scripts/har_study.py <base_url> <data_dir> <raw_dataset_dir> <out_json>
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import httpx

PW = "har-demo-password-1"
SPACE = {"lr": {"low": 1e-4, "high": 1e-2, "log": True}, "batch": {"low": 32, "high": 256, "log": True, "int": True},
         "dropout": {"low": 0.0, "high": 0.5}}
PROTOCOL = {"objective": "cnn.macro_f1", "factors": ["lr", "batch", "dropout"], "chance": 1 / 6,
            "search": {"sampler": "tpe", "max_trials": 10, "batch": 5, "patience_batches": 2},
            "confirm": {"top": 3, "seeds": 5}}


def main() -> int:
    base, data_dir, ds_dir, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    c = httpx.Client(base_url=base + "/api/v1", headers={"x-researchos-client": "har-study"}, timeout=120)
    k = lambda: {"idempotency-key": uuid.uuid4().hex}  # noqa: E731
    tok = data_dir / "config" / "setup-token.txt"
    if tok.is_file():
        c.post("/auth/setup", json={"username": "demo", "password": PW, "setup_token": tok.read_text().strip()},
               headers=k()).raise_for_status()
    else:
        c.post("/auth/login", json={"username": "demo", "password": PW}, headers=k()).raise_for_status()
    ds = c.post("/datasets", json={"path": str(ds_dir)}, headers=k()).json()
    manifest = json.loads((ds_dir / "manifest.json").read_text())
    tpl = next(t for t in c.get("/templates").json() if t["id"] == "har_cnn1d")
    content = {"hypothesis": "lr, batch and dropout affect 1D-CNN macro-F1 on HAR under a subject split",
               "template": {"id": "har_cnn1d", "hash": tpl["hash"]}, "params": {"epochs": 10}, "space": SPACE,
               "dataset": {"id": ds["id"], "manifest_sha256": ds["manifest_sha256"]},
               "split": {"kind": "group_counts", "seed": 2026,
                         "counts": {"train": 13, "search_val": 4, "confirm_val": 4},
                         "test": manifest["official_test_subjects"]},
               "metrics": ["cnn.macro_f1"], "limits": {"max_minutes": 30, "stall_s": 900, "max_trials": 80}}
    plan = c.post("/plans", json={"title": "HAR001 CNN study", "content": content}, headers=k()).json()
    sid = f"{plan['plan_id']}@1"
    h = c.get("/approvals/subject-hash", params={"subject_type": "plan", "subject_id": sid}).json()["hash"]
    c.post("/approvals", json={"subject_type": "plan", "subject_id": sid, "expected_hash": h},
           headers=k()).raise_for_status()
    st = c.post("/studies", json={"plan_id": plan["plan_id"], "revision": 1, "protocol": PROTOCOL}, headers=k())
    st.raise_for_status()
    t0 = time.time()
    while (s := c.get(f"/studies/{st.json()['id']}").json())["stage"] in ("screen", "search", "confirm", "control"):
        time.sleep(5)
    s["wall_s"] = round(time.time() - t0, 1)
    out.write_text(json.dumps(s, indent=1, default=str))
    print(s["stage"], s["wall_s"], "s", s["data"].get("stop_reason"))
    return 0 if s["stage"] == "awaiting_final_approval" else 1


if __name__ == "__main__":
    sys.exit(main())
