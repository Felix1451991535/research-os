"""M5 deep research end to end (mock models, in-test sources, synthetic PDFs, no network): plan -> owner approval
-> multi-round search (queries, then citation chaining) -> owner screening -> approved import incl. OA PDFs ->
parsing -> page-cited verified synthesis -> claims -> contradiction suggestion -> report with placeholders."""

from __future__ import annotations

import io
import json
import sqlite3
from typing import Any

import httpx
import pytest

from researchos.contracts.datasources import OaLocation
from researchos.contracts.gateway import ChatRequest
from researchos.services.container import Services
from researchos.services.gateway import catalog, certification
from researchos.services.knowledge.http_fetch import HostHttp
from researchos.services.research.deep_prompts import CONTRADICTIONS
from researchos.services.research.qa_prompts import VERIFY_SUPPORT

from .m1_helpers import configure_models, drain
from .m2_helpers import approve
from .m5_helpers import FakeOa, FakeSource, rec, register

Q = "How does heart rate variability change in condition A?"
TEXT = {"a": "Heart rate variability decreased in patients with condition A compared with healthy controls.",
        "b": "Heart rate variability increased in patients with condition A after an exercise programme."}


def _pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    c.drawString(60, 780, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def respond(req: ChatRequest) -> str:
    system, user = req.messages[0].content, req.messages[-1].content
    if "plan a literature search" in system:
        return json.dumps({"sub_questions": [{"q": Q, "queries": ["heart rate variability condition A"]}]})
    if "check whether each claim" in system:
        ids = [c["id"] for c in json.loads(user)] if user.startswith("[") else ["S1", "S2"]
        return json.dumps({"results": [{"id": i, "supported": True} for i in ids]})
    if "compare research claims" in system:
        return json.dumps({"pairs": [{"a": "K1", "b": "K2", "aspect": "direction of the HRV change",
                                      "explanation": "decrease vs increase"}]})
    labels = [x for x in ("E1", "E2") if f"[{x}]" in user]
    return json.dumps({"sentences": [{"text": "HRV was lower in condition A than in controls.", "evidence": [labels[0]],
                                      "kind": "source_fact"},
                                     {"text": "HRV rose after exercise in condition A.", "evidence": [labels[-1]],
                                      "kind": "source_fact"}], "confidence": 0.9})


@pytest.fixture
def world(svc: Services, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    for p in svc.gateway.providers.values():
        p.responder = respond  # type: ignore[attr-defined]
    configure_models(conn, svc)
    for a in catalog.list_all(conn):
        for prompt in (CONTRADICTIONS, VERIFY_SUPPORT):
            certification.record(conn, "test", a["model_id"], prompt.hash,
                                 {"locate_rate": 1, "support_precision": 1, "refusal_rate": 1})
    da, db, dc = "10.5555/dr.a", "10.5555/dr.b", "10.5555/dr.c"
    src = FakeSource("fake_dr", hits={"heart rate variability condition A": [
        rec("fake_dr", "a", "Heart rate variability in condition A", da),
        rec("fake_dr", "b", "Heart rate response to training in condition A", db),
        rec("fake_dr", "x", "Gut microbiome in mice", "10.5555/dr.x")]},
        graph={f"cited_by:{da}": [rec("fake_dr", "c", "Heart rate variability: a follow-up", dc)]})
    oa = FakeOa({da: OaLocation("https://repo.example.org/a.pdf", "CC-BY-4.0", "test"),
                 db: OaLocation("https://repo.example.org/b.pdf", "CC-BY-4.0", "test")})
    register(svc, src, oa)
    pdfs = {"/a.pdf": _pdf(TEXT["a"]), "/b.pdf": _pdf(TEXT["b"])}
    monkeypatch.setattr(HostHttp, "default_transport",
                        httpx.MockTransport(lambda r: httpx.Response(200, content=pdfs[r.url.path])))
    monkeypatch.setattr(HostHttp, "default_resolver", lambda host: ["93.184.216.34"])
    return {"src": src}


def step(client: Any, conn: sqlite3.Connection, svc: Services, rid: str) -> dict[str, Any]:
    client.post(f"/api/v1/research/deep/{rid}/advance")
    drain(conn, svc, max_tasks=20)
    return dict(client.get(f"/api/v1/research/deep/{rid}").json())


def test_deep_research_end_to_end(client: Any, conn: sqlite3.Connection, svc: Services, world: dict) -> None:
    client.put("/api/v1/literature/settings", json={"pdf_download": True})
    pid = client.post("/api/v1/projects", json={"title": "HRV"}).json()["id"]
    criteria = {"include": ["HRV in humans with condition A"], "exclude": ["animal studies"],
                "rules": [{"id": "hrv", "field": "title", "op": "contains", "value": "heart rate",
                           "decision": "include", "reason": "about heart rate"},
                          {"id": "mice", "field": "title", "op": "contains", "value": "mice", "decision": "exclude",
                           "reason": "animal study"}]}
    r = client.post("/api/v1/research/deep", json={"project_id": pid, "question": Q, "sources": ["fake_dr"],
                                                   "criteria": criteria})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    drain(conn, svc, max_tasks=5)
    st = client.get(f"/api/v1/research/deep/{rid}").json()
    assert st["stage"] == "awaiting_plan_approval" and st["plan"]["sub_questions"][0]["q"] == Q
    assert step(client, conn, svc, rid)["stage"] == "awaiting_plan_approval"  # no approval: no network search
    assert world["src"].calls == []
    approve(client, "research_checkpoint", f"{rid}:plan")
    st = step(client, conn, svc, rid)
    assert st["stage"] == "awaiting_screening" and st["prisma"]["records_screened"] == 0
    assert client.post(f"/api/v1/research/deep/{rid}/accept-suggestions").json()["decided"] == 3
    st = step(client, conn, svc, rid)  # round 2: chaining adds the follow-up paper
    assert st["stage"] == "awaiting_screening" and st["data"]["round2_new"] == 1
    client.post(f"/api/v1/research/deep/{rid}/accept-suggestions")
    st = step(client, conn, svc, rid)
    assert st["stage"] == "awaiting_import_approval" and len(st["data"]["selection"]) == 3
    approve(client, "knowledge_import", st["checkpoints"]["import"]["subject_id"])
    st = step(client, conn, svc, rid)
    for _ in range(4):
        if st["stage"] == "completed":
            break
        st = step(client, conn, svc, rid)
    assert st["stage"] == "completed", st["data"]
    p = st["prisma"]
    assert (p["records_identified"], p["records_screened"], p["records_excluded"]) == (4, 4, 1)
    report = client.get(f"/api/v1/projects/{pid}/notes").json()  # notes list = note kind only
    assert report == []
    doc = conn.execute("SELECT body FROM docs WHERE id=?", (st["data"]["report_doc"],)).fetchone()[0]
    assert "{{evidence:" in doc and "{{cite:" in doc and "direction of the HRV change" in doc
    assert "metadata only (no full text; not cited)" in doc  # the chained paper had no open PDF
    gate = client.get(f"/api/v1/docs/{st['data']['report_doc']}/check", params={"mode": "final"}).json()
    assert gate["ok"], gate["diagnostics"]  # the generated report is exportable as final (M6 gates)
    cl = client.get("/api/v1/claims", params={"project_id": pid}).json()
    assert len(cl["claims"]) == 2 and cl["contradictions"][0]["attrs"]["status"] == "suggested"
    c = cl["contradictions"][0]
    assert client.post("/api/v1/claims/contradictions/review",
                       json={"a": c["src"], "b": c["dst"], "status": "confirmed"}).json()["status"] == "confirmed"


def test_plan_supplied_by_owner_and_bad_plan(client: Any, svc: Services, world: dict) -> None:
    pid = client.post("/api/v1/projects", json={"title": "P"}).json()["id"]
    ok = client.post("/api/v1/research/deep", json={"project_id": pid, "question": Q, "sources": ["fake_dr"],
                                                    "sub_questions": [{"q": Q, "queries": ["hrv"]}]})
    assert ok.json()["stage"] == "awaiting_plan_approval"
    bad = client.post("/api/v1/research/deep", json={"project_id": pid, "question": Q, "sources": ["fake_dr"],
                                                     "sub_questions": [{"q": "x", "queries": []}]})
    assert bad.json()["code"] == "bad_plan"
