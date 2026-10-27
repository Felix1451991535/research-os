"""M2 helpers: register the synthetic dataset, create + approve plans for the fault fixture, run via the real
worker loop (template processes are real subprocesses)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from researchos.services.container import Services

from .conftest import Client
from .m1_helpers import drain

FIXTURE_TEMPLATES = Path(__file__).parent / "fixtures" / "templates"
SPLIT = {
    "kind": "group_counts",
    "seed": 1,
    "counts": {"train": 6, "search_val": 2, "confirm_val": 2},
    "test": ["s10", "s11"],
}


def template_hash(client: Client, tid: str) -> str:
    return next(t["hash"] for t in client.get("/api/v1/templates").json() if t["id"] == tid)


def plan_content(
    client: Client,
    dataset: dict[str, Any] | None,
    template: str = "fault",
    params: dict[str, Any] | None = None,
    **over: Any,
) -> dict[str, Any]:
    c: dict[str, Any] = {
        "hypothesis": "fixture runs behave as declared",
        "template": {"id": template, "hash": template_hash(client, template)},
        "metrics": ["macro_f1"],
        "limits": {"max_minutes": 2, "stall_s": 3},
        "params": params if params is not None else {"mode": "ok"},
        "predictions": [{"metric": "macro_f1", "expected": "> 0.5", "prior": 0.7}],
    }
    if dataset:
        c["dataset"] = {"id": dataset["id"], "manifest_sha256": dataset["manifest_sha256"]}
        c["split"] = SPLIT
    c.update(over)
    return c


def approve(client: Client, subject: str, sid: str) -> None:
    h = client.get("/api/v1/approvals/subject-hash", params={"subject_type": subject, "subject_id": sid}).json()["hash"]
    r = client.post("/api/v1/approvals", json={"subject_type": subject, "subject_id": sid, "expected_hash": h})
    assert r.status_code == 201, r.text


def new_plan(client: Client, content: dict[str, Any], approved: bool = True) -> str:
    r = client.post("/api/v1/plans", json={"title": "p", "content": content})
    assert r.status_code == 201, r.text
    pid = r.json()["plan_id"]
    if approved:
        approve(client, "plan", f"{pid}@1")
    return pid


def run(
    client: Client,
    conn: sqlite3.Connection,
    svc: Services,
    pid: str,
    rev: int = 1,
    stage: str = "trial",
    params: dict[str, Any] | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    r = client.post(
        f"/api/v1/plans/{pid}/revisions/{rev}/runs",
        json={
            "stage": stage,
            "params": params
            if params is not None
            else client.get(f"/api/v1/plans/{pid}/revisions/{rev}").json()["content"]["params"],
            "seed": seed,
        },
    )
    assert r.status_code == 202, r.text
    drain(conn, svc, max_tasks=1)
    t = client.get(f"/api/v1/tasks/{r.json()['id']}").json()
    runs = client.get(f"/api/v1/tasks/{t['id']}/runs").json()
    t["run"] = client.get(f"/api/v1/runs/{runs[-1]['id']}").json() if runs else None
    return t


def detectors_fired(t: dict[str, Any], severity: str = "fail") -> set[str]:
    return {s["detector"].split("@")[0] for s in (t["run"] or {}).get("signals", []) if s["severity"] == severity}
