"""M5 checkpoint 1: federated search with cross-source dedupe and per-source logging, citation chaining, approved
imports, retraction propagation, opt-in OA PDFs. In-test sources; no network, no paid calls."""

from __future__ import annotations

import sqlite3
from typing import Any

import httpx
import pytest

from researchos.contracts.datasources import OaLocation
from researchos.kernel.db import utcnow
from researchos.services.container import Services
from researchos.services.knowledge.http_fetch import HostHttp

from .m2_helpers import approve
from .m5_helpers import FakeOa, FakeSource, rec, register

DOI = "10.5555/syn.1"


@pytest.fixture
def sources(svc: Services) -> tuple[FakeSource, FakeSource]:
    a = FakeSource("fake_a", hits={"hrv": [rec("fake_a", "a1", "HRV review", DOI, kind="review"),
                                           rec("fake_a", "a2", "Primary study A", "10.5555/syn.2")]},
                   graph={f"cited_by:{DOI}": [rec("fake_a", "a9", "Citing paper", "10.5555/syn.9")]},
                   retracted={"10.5555/syn.2": True, DOI: False})
    b = FakeSource("fake_b", hits={"hrv": [rec("fake_b", "b1", "HRV Review (other source)", DOI.upper()),
                                           rec("fake_b", "b2", "No identifier study")]})
    register(svc, a, b)
    return a, b


def search(client: Any, text: str = "hrv", **kw: Any) -> dict[str, Any]:
    r = client.post("/api/v1/literature/search", json={"text": text, "sources": ["fake_b", "fake_a"], **kw})
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_federated_search_dedupes_by_doi_and_logs_each_source(client: Any, sources: Any) -> None:
    res = search(client, review_first=True)
    assert len(res["candidates"]) == 3  # DOI duplicate merged; no-identifier record kept separately (no title merge)
    first = res["candidates"][0]
    assert first["record"]["kind"] == "review" or first["record"]["title"].startswith("HRV")
    merged = next(c for c in res["candidates"] if c["dedupe_key"] == f"doi:{DOI}")
    assert {p["source"] for p in merged["provenance"]} == {"fake_a", "fake_b"}
    assert res["per_source"]["fake_a"]["returned"] == 2 and res["per_source"]["fake_b"]["returned"] == 2
    again = search(client)
    assert again["new_candidate_ids"] == [] and set(again["candidate_ids"]) == set(res["candidate_ids"])


def test_one_failing_source_is_recorded_not_hiding_others(client: Any, sources: Any) -> None:
    sources[1].hits["fail"] = []
    res = search(client, "fail")
    assert "error" in res["per_source"]["fake_a"] and "HttpUnavailable" in res["per_source"]["fake_a"]["error"]
    assert "error" in res["per_source"]["fake_b"]
    run = client.get(f"/api/v1/literature/runs/{res['id']}").json()
    assert run["per_source"] == res["per_source"]


def test_citation_chaining_forward(client: Any, sources: Any) -> None:
    seed = next(c for c in search(client)["candidates"] if c["dedupe_key"] == f"doi:{DOI}")
    r = client.post("/api/v1/literature/chain", json={"candidate_id": seed["id"], "direction": "cited_by"}).json()
    assert r["kind"] == "cited_by" and [c["record"]["title"] for c in r["candidates"]] == ["Citing paper"]
    assert set(r["per_source"]) == {"fake_a", "fake_b"}  # both fakes implement the capability


def test_import_needs_approval_of_exact_selection(client: Any, sources: Any, conn: sqlite3.Connection) -> None:
    ids = [c["id"] for c in search(client)["candidates"]]
    sel = {"candidate_ids": ids[:2]}
    assert client.post("/api/v1/literature/import", json=sel).json()["code"] == "import_not_approved"
    h = client.post("/api/v1/literature/import/selection-hash", json=sel).json()
    approve(client, "knowledge_import", h["subject_id"])
    res = client.post("/api/v1/literature/import", json=sel).json()["results"]
    assert all(r["status"] == "created" and r["work_id"] for r in res)
    assert client.get(f"/api/v1/literature/candidates/{ids[0]}").json()["work_id"] == res[0]["work_id"]
    assert client.post("/api/v1/literature/import", json=sel).json()["code"] == "import_not_approved"  # consumed


def _imported_work_with_evidence(client: Any, conn: sqlite3.Connection, doi: str) -> tuple[str, str]:
    cid = next(c["id"] for c in search(client)["candidates"] if c["dedupe_key"] == f"doi:{doi}")
    h = client.post("/api/v1/literature/import/selection-hash", json={"candidate_ids": [cid]}).json()["subject_id"]
    approve(client, "knowledge_import", h)
    wid = client.post("/api/v1/literature/import", json={"candidate_ids": [cid]}).json()["results"][0]["work_id"]
    now = utcnow()
    conn.execute("INSERT INTO document_versions(version_id,work_id,sha256,stored_path,original_name,size,mime,"
                 "parse_state,created_at) VALUES ('ver_r',?,?,'raw/x.pdf','x.pdf',1,'application/pdf','parsed',?)",
                 (wid, "0" * 64, now))
    conn.execute("INSERT INTO evidence(id,version_id,page_index,char_start,char_end,quote,quote_sha256,"
                 "page_text_sha256,parser_version,created_by,created_at) VALUES ('ev_r','ver_r',0,0,5,'quote','q',"
                 "'p','1','test',?)", (now,))
    conn.execute("INSERT INTO answers(id,question,status,body,created_at) VALUES ('ans_r','q','answered',?,?)",
                 ('{"sentences":[{"evidence":["ev_r"]}]}', now))
    conn.commit()
    return wid, "ev_r"


def test_retraction_fails_evidence_and_alerts(client: Any, sources: Any, conn: sqlite3.Connection) -> None:
    wid, ev = _imported_work_with_evidence(client, conn, "10.5555/syn.2")
    res = client.post("/api/v1/literature/retractions/check", json={"work_ids": [wid]}).json()
    assert res["retracted"][0]["work_id"] == wid and res["retracted"][0]["affected_answers"] == ["ans_r"]
    assert conn.execute("SELECT trust FROM evidence WHERE id=?", (ev,)).fetchone()[0] == "failed"
    alerts = client.get("/api/v1/alerts", params={"unread": True}).json()
    assert alerts[0]["kind"] == "retraction" and alerts[0]["data"]["evidence_failed"] == 1
    again = client.post("/api/v1/literature/retractions/check", json={"work_ids": [wid]}).json()
    assert again["retracted"] == []  # no duplicate alert


def test_unknown_retraction_status_stays_unknown(client: Any, sources: Any, conn: sqlite3.Connection) -> None:
    sources[0].retracted.pop(DOI)
    ids = [c["id"] for c in search(client)["candidates"] if c["dedupe_key"] == f"doi:{DOI}"]
    h = client.post("/api/v1/literature/import/selection-hash", json={"candidate_ids": ids}).json()["subject_id"]
    approve(client, "knowledge_import", h)
    wid = client.post("/api/v1/literature/import", json={"candidate_ids": ids}).json()["results"][0]["work_id"]
    res = client.post("/api/v1/literature/retractions/check", json={"work_ids": [wid]}).json()
    assert res["unknown"] == [DOI] and res["retracted"] == []


def test_oa_pdf_only_when_enabled_and_explicitly_licensed(client: Any, svc: Services, sources: Any,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    pdf_url = "https://repo.example.org/syn1.pdf"
    oa = FakeOa({DOI: OaLocation(pdf_url, None, None, "publishedVersion", "repository")})
    register(svc, oa)
    monkeypatch.setattr(HostHttp, "default_transport",
                        httpx.MockTransport(lambda req: httpx.Response(200, content=b"%PDF-1.4\n%synthetic\n")))
    monkeypatch.setattr(HostHttp, "default_resolver", lambda host: ["93.184.216.34"])
    ids = [c["id"] for c in search(client)["candidates"] if c["dedupe_key"] == f"doi:{DOI}"]

    def imp() -> dict[str, Any]:
        h = client.post("/api/v1/literature/import/selection-hash", json={"candidate_ids": ids}).json()["subject_id"]
        approve(client, "knowledge_import", h)
        return dict(client.post("/api/v1/literature/import",
                                json={"candidate_ids": ids, "with_pdf": True}).json()["results"][0])

    assert "PDF download is off" in imp()["pdf"]["skipped"]
    client.put("/api/v1/literature/settings", json={"pdf_download": True})
    assert "no explicit open licence" in imp()["pdf"]["skipped"]
    oa.locations[DOI] = OaLocation(pdf_url, "CC-BY-4.0", "unpaywall:best_oa_location.license")
    stored = imp()["pdf"]
    assert stored["version_id"] and stored["location"]["license"] == "CC-BY-4.0"


def test_real_litsearch_plugin_through_host_http(client: Any, svc: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    """The merged litsearch plugin (Crossref + OpenAlex) via the core search endpoint and HostHttp (mock transport,
    synthetic CC0 fixtures shipped with the plugin)."""
    pytest.importorskip("researchos_plugin_litsearch")
    from pathlib import Path

    from researchos_plugin_litsearch import MANIFEST
    from researchos_plugin_litsearch.json_sources import CrossrefSource
    from researchos_plugin_litsearch.openalex_graph import OpenAlexSource

    fx = Path(__file__).parents[1] / "plugins" / "litsearch" / "fixtures"
    bodies = {"api.crossref.org": (fx / "crossref.json").read_bytes(), "api.openalex.org": (fx / "openalex.json")
              .read_bytes()}
    monkeypatch.setattr(HostHttp, "default_transport",
                        httpx.MockTransport(lambda req: httpx.Response(200, content=bodies[req.url.host])))
    monkeypatch.setattr(HostHttp, "default_resolver", lambda host: ["93.184.216.34"])
    for src in (CrossrefSource(), OpenAlexSource()):
        svc.extensions.sources[src.spec.name] = src
        svc.extensions.owners[f"datasources:{src.spec.name}"] = MANIFEST
    r = client.post("/api/v1/literature/search", json={"text": "synthetic", "sources": ["lit_crossref",
                                                                                          "lit_openalex"]}).json()
    assert r["per_source"]["lit_crossref"]["returned"] >= 1 and r["per_source"]["lit_openalex"]["returned"] >= 1
    assert all(c["record"]["title"] for c in r["candidates"])


def test_orientation_mode_reviews_first_one_hop_and_map(client: Any, svc: Services) -> None:
    rv1, rv2 = rec("fake_o", "r1", "Survey of HRV", "10.5555/o.r1", kind="review"), \
        rec("fake_o", "r2", "Review of wearables", "10.5555/o.r2", kind="review")
    hub = rec("fake_o", "h", "Landmark HRV paper", "10.5555/o.h", year=2001)
    src = FakeSource("fake_o", hits={"hrv": [rec("fake_o", "p", "Primary study", "10.5555/o.p"), rv1, rv2]},
                     graph={"references:10.5555/o.r1": [hub], "references:10.5555/o.r2": [hub],
                            "cited_by:10.5555/o.r1": [rec("fake_o", "n", "New follow-up", "10.5555/o.n")]})
    register(svc, src)
    r = client.post("/api/v1/literature/orientation", json={"text": "hrv", "sources": ["fake_o"]}).json()
    assert [c["record"]["kind"] for c in r["candidates"][:2]] == ["review", "review"]
    assert len(r["seeds"]) == 2 and set(r["seeds"]) == set(r["reviews"])
    assert len(r["runs"]) == 5  # 1 search + 2 seeds x (references, cited_by)
    hubs = r["map"]["hubs"]
    assert len(hubs) == 1 and hubs[0]["reached_from"] == 2
    assert r["map"]["by_kind"] == {"article": 3, "review": 2} and r["map"]["by_year"]["2001"] == 1
