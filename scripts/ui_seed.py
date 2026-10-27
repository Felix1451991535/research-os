"""Seed a RUNNING throwaway Research OS instance through the real API with SYNTHETIC content for UI review and the
release video: account, project with note + manuscript, synthetic dataset, a passing and a failing experiment run
(fixture template `fault`), and a planted-surface study (fixture template `planted`). Prints ids as JSON.

Usage (dev tool): python scripts/ui_seed.py <base_url> <data_dir>   (data dir config must list tests/fixtures/templates)
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.fixtures.make_dataset import make  # noqa: E402  (synthetic data generator shared with the tests)

PW = "correct-horse-battery"
SPLIT = {"kind": "group_counts", "seed": 1, "counts": {"train": 6, "search_val": 2, "confirm_val": 2},
         "test": ["s10", "s11"]}
STUDY = {"objective": "score", "factors": ["lr", "batch", "dropout"], "chance": 1 / 3,
         "search": {"sampler": "qmc", "max_trials": 10, "batch": 5, "patience_batches": 2},
         "confirm": {"top": 3, "seeds": 5}}
SPACE = {"lr": {"low": 0.0001, "high": 1.0, "log": True}, "batch": {"low": 16, "high": 128, "int": True, "log": True},
         "dropout": {"low": 0.0, "high": 0.5}}


def client(base: str, data_dir: Path) -> httpx.Client:
    c = httpx.Client(base_url=base + "/api/v1", headers={"x-researchos-client": "ui-seed"}, timeout=60)
    token = (data_dir / "config" / "setup-token.txt").read_text().strip()
    c.post("/auth/setup", json={"username": "researcher", "password": PW, "setup_token": token}).raise_for_status()
    return c


def post(c: httpx.Client, path: str, body: dict[str, Any]) -> Any:
    r = c.post(path, json=body, headers={"idempotency-key": uuid.uuid4().hex})
    r.raise_for_status()
    return r.json()


def approved_plan(c: httpx.Client, title: str, content: dict[str, Any]) -> str:
    pid = str(post(c, "/plans", {"title": title, "content": content})["plan_id"])
    h = c.get("/approvals/subject-hash", params={"subject_type": "plan", "subject_id": f"{pid}@1"}).json()["hash"]
    post(c, "/approvals", {"subject_type": "plan", "subject_id": f"{pid}@1", "expected_hash": h})
    return pid


def wait(c: httpx.Client, task_id: str, timeout_s: float = 300) -> dict[str, Any]:
    end = time.time() + timeout_s
    while (t := c.get(f"/tasks/{task_id}").json())["state"] in ("queued", "running") and time.time() < end:
        time.sleep(1)
    return dict(t)


def main() -> int:
    base, data_dir = sys.argv[1], Path(sys.argv[2])
    c = client(base, data_dir)
    project = post(c, "/projects", {"title": "HAR001 — activity recognition baseline",
                                    "goal": "Subject-split baselines on synthetic HAR-like windows (demo data)."})
    post(c, f"/projects/{project['id']}/notes", {"title": "Reading plan", "body": "1. Split protocol\n2. Baselines"})
    doc = post(c, f"/projects/{project['id']}/docs", {"kind": "manuscript", "title": "HAR001 draft",
               "body": "# Results\n\nThe baseline reached a macro-F1 reported in the run record."})
    ds = post(c, "/datasets", {"path": str(make(data_dir / "seed_dataset"))})
    tpl = {t["id"]: t["hash"] for t in c.get("/templates").json()}
    common = {"template": {"id": "fault", "hash": tpl["fault"]}, "metrics": ["macro_f1"],
              "dataset": {"id": ds["id"], "manifest_sha256": ds["manifest_sha256"]}, "split": SPLIT,
              "limits": {"max_minutes": 2, "stall_s": 30},
              "predictions": [{"metric": "macro_f1", "expected": "> 0.5", "prior": 0.7}]}
    runs = {}
    for name, mode in (("passing", "ok"), ("failing", "nan")):
        pid = approved_plan(c, f"Synthetic run ({name})", {**common, "hypothesis": f"fixture {name}",
                                                            "params": {"mode": mode}})
        t = post(c, f"/plans/{pid}/revisions/1/runs", {"stage": "trial", "params": {"mode": mode}, "seed": 0})
        runs[name] = {"plan_id": pid, "task_id": t["id"], "state": wait(c, t["id"])["state"]}
    spid = approved_plan(c, "Planted surface study", {
        **common, "template": {"id": "planted", "hash": tpl["planted"]}, "params": {}, "space": SPACE,
        "metrics": ["score"], "hypothesis": "lr dominates; dropout is null",
        "limits": {"max_minutes": 2, "stall_s": 30, "max_trials": 60}})
    study = post(c, "/studies", {"plan_id": spid, "revision": 1, "protocol": STUDY})
    end = time.time() + 600
    sid = study["id"]
    while c.get(f"/studies/{sid}").json().get("stage") not in ("awaiting_final_approval", "stopped") \
            and time.time() < end:
        time.sleep(2)
    print(json.dumps({"project_id": project["id"], "doc_id": doc["id"], "runs": runs, "study_id": study["id"],
                      "study_stage": c.get(f"/studies/{study['id']}").json().get("stage")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
