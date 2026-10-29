"""Methods checklist (ADR-180): page-cited items from this work only, deterministic missing conditions."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from researchos.contracts.gateway import ChatRequest
from researchos.services.container import Services
from researchos.services.content.texts import CORE
from researchos.services.gateway import catalog, certification
from researchos.services.research.qa_prompts import VERIFY_SUPPORT
from researchos.services.writing import methods_checklist

from .m1_helpers import configure_models, drain
from .test_reading import _pdf, _pdf_other


def test_checklist_cites_pages_and_lists_missing_conditions(client: Any, conn: sqlite3.Connection,
                                                           svc: Services, language: str = "en") -> None:
    def respond(req: ChatRequest) -> str:
        if "check whether each claim" in req.messages[0].content:
            return json.dumps({"results": [{"id": "S1", "supported": True}]})
        if "is the split by subject" in req.messages[-1].content:  # only the split item has an answer
            return json.dumps({"sentences": [{"text": "The evaluation uses held-out subjects only.",
                                              "evidence": ["E1"], "kind": "source_fact"}], "confidence": 0.9})
        return json.dumps({"sentences": [], "confidence": 0.1})

    for p in svc.gateway.providers.values():
        p.responder = respond  # type: ignore[attr-defined]
    configure_models(conn, svc)
    for a in catalog.list_all(conn):
        certification.record(conn, "test", a["model_id"], VERIFY_SUPPORT.hash,
                             {"locate_rate": 1, "support_precision": 1, "refusal_rate": 1})
    pid = client.post("/api/v1/projects", json={"title": "Checklist"}).json()["id"]
    up = [client.post("/api/v1/documents/upload", data={"project_id": pid},
                      files={"file": (n, b, "application/pdf")}).json()["version_id"]
          for n, b in (("p.pdf", _pdf()), ("other.pdf", _pdf_other()))]
    drain(conn, svc)
    work = conn.execute("SELECT work_id FROM document_versions WHERE version_id=?", (up[0],)).fetchone()[0]
    r = client.post(f"/api/v1/works/{work}/methods-checklist", json={"project_id": pid, "language": language},
                    headers={"idempotency-key": f"mc-{language}"})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["doc"]["kind"] == "methods_checklist"
    cited = {k for k, f in out["fields"].items() if f.get("cited")}
    assert cited == {"split"}
    for s in client.get(f"/api/v1/docs/{out['doc']['id']}/suggestions", params={"status": "proposed"}).json():
        client.post(f"/api/v1/suggestions/{s['id']}/decision", json={"accept": True})
    T = CORE.lang(language)
    body = client.get(f"/api/v1/docs/{out['doc']['id']}").json()["body"]
    owner = f"## {T('card_methods_checklist_owner')}\n\n{T('card_owner_placeholder')}"
    assert "_(pending" not in body and owner in body
    ev = [x.split("}}")[0] for x in body.split("{{evidence:")[1:]]
    pages = conn.execute(f"SELECT v.work_id, e.page_index FROM evidence e JOIN document_versions v "
                         f"ON v.version_id=e.version_id WHERE e.id IN ({','.join('?' * len(ev))})", ev).fetchall()
    assert ev and {w for w, _ in pages} == {work} and all(p is not None for _, p in pages)  # page-located
    tail = body.split(f"## {T('card_methods_checklist_missing')}")[1].split(owner)[0]
    lines = tail.strip().splitlines()
    assert lines == [T("mc_not_established", item=methods_checklist.SPEC.heading(T, k))
                     for k, _ in methods_checklist.ITEMS if k != "split"]  # every gap, in order
    assert methods_checklist.SPEC.heading(T, "split") not in tail
    if language == "zh-CN":
        assert body.startswith("方法核查表：{{cite:")
        for english in ("Missing conditions", "Reproduction notes", "Metrics", "not established"):
            assert english not in body, english


def test_checklist_in_chinese_keeps_the_same_evidence(client: Any, conn: sqlite3.Connection, svc: Services) -> None:
    """ADR-246: the card's fixed text (title, headings, gap lines, owner placeholder) in zh-CN; answers, evidence
    markers and the cited / established logic do not depend on the language."""
    test_checklist_cites_pages_and_lists_missing_conditions(client, conn, svc, language="zh-CN")


def test_missing_conditions_in_chinese() -> None:
    done = {k: {"cited": True, "status": "answered", "kinds": {"source_fact": 1}} for k, _ in methods_checklist.ITEMS}
    done["metrics"] = {"cited": False, "status": "insufficient_material"}
    done["statistics"] = {"cited": False, "status": "not_configured"}
    zh = CORE.lang("zh-CN")
    assert methods_checklist.missing(None, {}, done, zh)[0][1].splitlines() == [  # type: ignore[arg-type]
        "- 评价指标：检索并验证的证据未能确立", "- 统计：未解决，该问题无法核查（not_configured）"]


def test_missing_conditions_is_deterministic() -> None:
    done = {k: {"cited": True, "status": "answered", "kinds": {"source_fact": 1}}
            for k, _ in methods_checklist.ITEMS}
    assert "None" in methods_checklist.missing(None, {}, done)[0][1]  # type: ignore[arg-type]
    done["metrics"] = {"cited": False, "status": "insufficient_material"}
    assert methods_checklist.missing(None, {}, done)[0][1] == \
        "- Metrics: not established by retrieved, verified evidence"  # type: ignore[arg-type]


def test_67_unverified_is_not_reported_as_absent_from_the_paper() -> None:
    """Review #67: a failed retrieval/verification must not read as "the paper does not state it"; an item whose
    question could not be checked is unresolved, listed apart from not-established items."""
    done = {k: {"cited": True, "status": "answered", "kinds": {"source_fact": 1}}
            for k, _ in methods_checklist.ITEMS}
    done["metrics"] = {"cited": False, "status": "insufficient_material"}
    done["statistics"] = {"cited": False, "status": "not_configured"}
    text = methods_checklist.missing(None, {}, done)[0][1]  # type: ignore[arg-type]
    assert "full text" not in text and "not stated" not in text
    assert text.splitlines() == ["- Metrics: not established by retrieved, verified evidence",
                                 "- Statistics: unresolved, the question could not be checked (not_configured)"]


def test_199_inference_or_meta_statements_do_not_establish_an_item() -> None:
    """V1 acceptance finding: "The provided passages do not specify filter cut-offs" (model_inference, and once
    mislabelled author_conclusion) made Preprocessing count as cited. Only a source_fact establishes an item."""
    done = {k: {"cited": True, "status": "answered", "kinds": {"source_fact": 1}}
            for k, _ in methods_checklist.ITEMS}
    done["preprocessing"] = {"cited": True, "status": "answered", "kinds": {"model_inference": 1}}
    done["data"] = {"cited": True, "status": "answered", "kinds": {"author_conclusion": 1}}
    text = methods_checklist.missing(None, {}, done)[0][1]  # type: ignore[arg-type]
    assert text.splitlines() == ["- Data source and licence: not established by retrieved, verified evidence",
                                 "- Preprocessing: not established by retrieved, verified evidence"]
