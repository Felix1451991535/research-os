"""M0 projects (idempotency, revision) and notes (conflict keeps both, deduped by client_operation_id)."""
from __future__ import annotations

from .conftest import Client


def test_projects_idempotency_and_revision(client: Client) -> None:
    h = {"idempotency-key": "same-key"}
    a = client.post("/api/v1/projects", json={"title": "HAR", "goal": "g"}, headers=h)
    b = client.post("/api/v1/projects", json={"title": "HAR", "goal": "g"}, headers=h)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert len(client.get("/api/v1/projects").json()) == 1
    c = client.post("/api/v1/projects", json={"title": "other"}, headers=h)
    assert c.status_code == 422 and c.json()["code"] == "idempotency_mismatch"
    assert client.request("POST", "/api/v1/projects", json={"title": "x"}, idem=False).status_code == 400
    pid = a.json()["id"]
    r = client.patch(f"/api/v1/projects/{pid}", json={"revision": 1, "goal": "new"})
    assert r.status_code == 200 and r.json()["revision"] == 2
    r = client.patch(f"/api/v1/projects/{pid}", json={"revision": 1, "goal": "stale"})
    assert r.status_code == 409 and r.json()["details"]["current_revision"] == 2
    kinds = [e["kind"] for e in client.get("/api/v1/events", params={"project_id": pid}).json()]
    assert kinds == ["project.created", "project.updated"]


def test_note_conflict_keeps_both_and_dedupes_resync(client: Client) -> None:
    pid = client.post("/api/v1/projects", json={"title": "p"}).json()["id"]
    n = client.post(f"/api/v1/projects/{pid}/notes", json={"title": "t", "body": "v1"}).json()
    assert n["kind"] == "note"
    assert client.put(f"/api/v1/notes/{n['id']}", json={"title": "t", "body": "server", "base_version": 1}
                      ).status_code == 200
    stale = {"title": "t", "body": "offline", "base_version": 1, "client_operation_id": "op-1"}
    r1 = client.put(f"/api/v1/notes/{n['id']}", json=stale)
    r2 = client.put(f"/api/v1/notes/{n['id']}", json=stale)  # offline client retries the same sync
    assert r1.status_code == r2.status_code == 409
    d = r1.json()["details"]
    assert d["server"]["body"] == "server" and d["client_body"] == "offline"
    assert d["conflict_copy_id"] == r2.json()["details"]["conflict_copy_id"]
    bodies = sorted(x["body"] for x in client.get(f"/api/v1/projects/{pid}/notes").json())
    assert bodies == ["offline", "server"]
