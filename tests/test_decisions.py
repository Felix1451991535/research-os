"""Decision records (ADR-184): owner-written, linked, immutable; support recomputed from live links."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from researchos.kernel.db import jdump, utcnow
from researchos.services.container import Services

from .fixtures.make_dataset import make
from .m2_helpers import new_plan, plan_content, run


def _claim(conn: sqlite3.Connection, cid: str, project_id: str, trust: str = "machine_checked") -> str:
    now = utcnow()
    conn.execute("INSERT INTO entities(id,type,project_id,data,trust,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                 (cid, "claim", project_id, jdump({"text": "t", "evidence_ids": [], "work_ids": []}), trust, now, now))
    conn.commit()
    return cid


def _post(client: Any, pid: str, key: str, **over: Any) -> Any:
    body = {"kind": "split", "question": "How are subjects split?", "choice": "subject-wise, seeded",
            "alternatives": [{"option": "trial-wise", "why_not": "leaks subject identity"}],
            "rationale": "Subject-wise splits match how the model would be used.", **over}
    return client.post(f"/api/v1/projects/{pid}/decisions", json=body, headers={"idempotency-key": key})


def test_decision_support_links_and_supersede(client: Any, conn: sqlite3.Connection, svc: Services,
                                              tmp_path: Path) -> None:
    pid = client.post("/api/v1/projects", json={"title": "Decisions"}).json()["id"]
    first = _post(client, pid, "d-1")
    assert first.status_code == 201, first.text
    assert first.json()["support"] == "owner_judgement" and first.json()["links"] == []
    assert _post(client, pid, "d-1").json()["id"] == first.json()["id"]  # idempotent replay

    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    plan = new_plan(client, plan_content(client, ds))
    run_id = run(client, conn, svc, plan)["run"]["id"]
    claim = _claim(conn, "claim_x", pid)
    links = [f"plan:{plan}@1", f"run:{run_id}", f"claim:{claim}"]
    second = _post(client, pid, "d-2", links=links, supersedes=first.json()["id"]).json()
    assert second["support"] == "supported" and second["supersedes"] == first.json()["id"]
    assert [x["ref"] for x in second["links"]] == links and second["broken_links"] == []
    listed = client.get(f"/api/v1/projects/{pid}/decisions").json()
    assert [d["id"] for d in listed] == [second["id"]]
    every = client.get(f"/api/v1/projects/{pid}/decisions", params={"include_superseded": True}).json()
    assert [d["superseded_by"] for d in every] == [second["id"], None]
    again = _post(client, pid, "d-3", supersedes=first.json()["id"])
    assert again.status_code == 409  # history is linear: supersede the newest record

    conn.execute("UPDATE entities SET trust='failed' WHERE id=?", (claim,))
    conn.execute("UPDATE runs SET state='failed' WHERE id=?", (run_id,))
    conn.commit()
    now = client.get(f"/api/v1/decisions/{second['id']}").json()
    assert now["broken_links"] == [f"run:{run_id}", f"claim:{claim}"] and now["support"] == "owner_judgement"


def test_bad_unknown_and_foreign_links_are_rejected(client: Any, conn: sqlite3.Connection) -> None:
    pid = client.post("/api/v1/projects", json={"title": "D2"}).json()["id"]
    other = client.post("/api/v1/projects", json={"title": "D3"}).json()["id"]
    foreign = _claim(conn, "claim_other", other)
    for i, links in enumerate((["evidence"], ["file:/etc/passwd"], ["run:nope"], [f"claim:{foreign}"],
                               ["plan:nope@1"])):
        r = _post(client, pid, f"bad-{i}", links=links)
        assert r.status_code == 422, (links, r.text)
    assert _post(client, pid, "bad-kind", kind="vibes").status_code == 422
    assert client.get(f"/api/v1/projects/{pid}/decisions").json() == []
