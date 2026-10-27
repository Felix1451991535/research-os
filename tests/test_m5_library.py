"""M5 checkpoint 3: citekeys bound to evidence, BibTeX/RIS/CSL-JSON export (escaped, retraction-flagged, round-trip
through the merged parsers), tags/collections, backlinks from notes, global search, file imports -> candidates."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from researchos.services.container import Services

from .m2_helpers import approve
from .m5_helpers import FakeSource, rec, register

ROOT = Path(__file__).parents[1]


def imported(client: Any, svc: Services, records: list[Any]) -> list[str]:
    register(svc, FakeSource("fake_lib", hits={"q": records}))
    ids = [c["id"] for c in client.post("/api/v1/literature/search", json={"text": "q", "sources": ["fake_lib"]})
           .json()["candidates"]]
    h = client.post("/api/v1/literature/import/selection-hash", json={"candidate_ids": ids}).json()["subject_id"]
    approve(client, "knowledge_import", h)
    return [r["work_id"] for r in client.post("/api/v1/literature/import", json={"candidate_ids": ids})
            .json()["results"]]


@pytest.fixture
def works(client: Any, svc: Services) -> list[str]:
    return imported(client, svc, [
        rec("fake_lib", "1", "Heart rate & variability_in adults", "10.5555/l.1"),
        rec("fake_lib", "2", "Heart rhythms revisited", "10.5555/l.2"),
        rec("fake_lib", "3", "心率变异性研究", "10.5555/l.3"),
    ])


def test_citekeys_collisions_nonlatin_and_evidence_binding(client: Any, works: list[str],
                                                            conn: sqlite3.Connection) -> None:
    keys = [client.post(f"/api/v1/works/{w}/citekey").json()["citekey"] for w in works]
    assert keys[0] == "author2024heart" and keys[1] == "author2024hearta" and keys[2] == "author2024"
    assert client.post(f"/api/v1/works/{works[0]}/citekey").json()["citekey"] == keys[0]  # stable
    conn.execute("UPDATE works SET authors='[\"张三\"]' WHERE id=?", (works[2],))
    conn.execute("DELETE FROM citekeys WHERE work_id=?", (works[2],))
    conn.commit()
    k3 = client.post(f"/api/v1/works/{works[2]}/citekey").json()["citekey"]
    assert k3.startswith("ref2024-") and len(k3) == len("ref2024-") + 4
    r = client.get(f"/api/v1/citekeys/{keys[0]}").json()
    assert r["work"]["id"] == works[0] and r["evidence"] == [] and r["versions"] == []


def test_exports_escape_flag_retractions_and_round_trip(client: Any, works: list[str],
                                                        conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE works SET retraction=? WHERE id=?", (json.dumps({"retracted": True}), works[1]))
    conn.commit()
    bib = client.post("/api/v1/library/export", json={"format": "bibtex"}).text
    assert r"Heart rate \& variability\_in adults" in bib and "note = {RETRACTED}" in bib
    assert "doi = {10.5555/l.1}" in bib and bib.count("@article{") == 3
    ris = client.post("/api/v1/library/export", json={"format": "ris"}).text
    assert ris.count("ER  - ") == 3 and "N1  - RETRACTED" in ris
    csl = json.loads(client.post("/api/v1/library/export", json={"format": "csl-json", "work_ids": [works[0]]}).text)
    assert csl[0]["DOI"] == "10.5555/l.1" and csl[0]["issued"] == {"date-parts": [[2024]]}
    pytest.importorskip("researchos_plugin_litsearch")
    from researchos_plugin_litsearch.importers import BibtexImporter, RisImporter

    from researchos.contracts.importers import FileImportRequest

    for parser, text in ((BibtexImporter(), bib), (RisImporter(), ris)):
        back = parser.parse_file(FileImportRequest("x", text.encode()))
        assert sorted(dict(r.identifiers).get("doi") for r in back) == ["10.5555/l.1", "10.5555/l.2", "10.5555/l.3"]


def test_tags_collections_backlinks_global_search(client: Any, works: list[str]) -> None:
    client.put(f"/api/v1/works/{works[0]}/tags", json={"names": ["hrv", "adults", "hrv"]})
    client.put(f"/api/v1/works/{works[0]}/collections", json={"names": ["thesis ch2"]})
    listed = client.get("/api/v1/library/works", params={"tag": "hrv"}).json()
    assert [w["id"] for w in listed] == [works[0]] and listed[0]["tags"] == ["adults", "hrv"]
    assert client.get("/api/v1/library/works", params={"collection": "thesis ch2"}).json()[0]["id"] == works[0]
    assert client.put(f"/api/v1/works/{works[0]}/tags", json={"names": ["bad\nname"]}).json()["code"] == "bad_label"
    key = client.post(f"/api/v1/works/{works[0]}/citekey").json()["citekey"]
    pid = client.post("/api/v1/projects", json={"title": "P"}).json()["id"]
    note = client.post(f"/api/v1/projects/{pid}/notes", headers={"idempotency-key": uuid.uuid4().hex},
                       json={"title": "Reading", "body": f"Key result {{{{cite:{key}}}}} here"}).json()
    assert [b["id"] for b in client.get(f"/api/v1/works/{works[0]}/backlinks").json()] == [note["id"]]
    g = client.get("/api/v1/search/global", params={"q": "rhythms"}).json()
    assert [w["id"] for w in g["works"]] == [works[1]]
    assert client.get("/api/v1/search/global", params={"q": "Key result"}).json()["docs"][0]["id"] == note["id"]


def test_bibliography_file_becomes_logged_candidates(client: Any, svc: Services) -> None:
    pytest.importorskip("researchos_plugin_zotero_import")
    from researchos_plugin_zotero_import.csljson import ZoteroCslJsonImporter

    svc.extensions.file_importers["zotero_csljson"] = ZoteroCslJsonImporter()
    data = (ROOT / "plugins/zotero_import/fixtures/zotero_export.csl.json").read_bytes()
    r = client.post("/api/v1/literature/files/zotero_csljson", files={"file": ("lib.json", data, "application/json")})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["per_source"]["zotero_csljson"]["returned"] >= len(run["candidates"])  # in-file duplicates merge
    assert run["candidates"] and run["query"]["importer"] == "zotero_csljson"
    bad = client.post("/api/v1/literature/files/zotero_csljson", files={"file": ("x.json", b"{not json", "x")})
    assert bad.json()["code"] == "unparseable_file"


def test_evidence_weight_labels_never_guessed(client: Any, works: list[str], conn: sqlite3.Connection) -> None:
    w = client.get(f"/api/v1/works/{works[0]}/weight").json()
    assert w["publication"] == "published" and "tier unknown" in w["labels"] and w["tiers"] == []
    assert "retraction status unknown" in w["labels"]
    r = client.put("/api/v1/library/venue-tiers", json={"source": "JCR", "year": 2025,
                                                        "entries": [{"venue": "Synthetic  journal", "tier": "Q1"}]})
    assert r.json()["entries"] == 1
    w = client.get(f"/api/v1/works/{works[0]}/weight").json()
    assert w["tiers"] == [{"tier": "Q1", "source": "JCR", "year": 2025}] and "Q1 (JCR 2025)" in w["labels"]
    conn.execute("UPDATE works SET kind='preprint', retraction=? WHERE id=?", (json.dumps({"retracted": True}),
                                                                             works[1]))
    conn.commit()
    w2 = client.get(f"/api/v1/works/{works[1]}/weight").json()
    assert w2["labels"][0] == "RETRACTED" and w2["publication"] == "preprint"
    listed = {x["id"]: x["weight"] for x in client.get("/api/v1/library/works").json()}
    assert listed[works[1]][0] == "RETRACTED"


def test_library_listing_parses_tier_tables_once(client: Any, works: list[str], conn: sqlite3.Connection) -> None:
    """Regression (U2): the venue-tier JSON is read once per listing, not once per work."""
    from researchos.services.literature import library_index

    client.put("/api/v1/library/venue-tiers", json={"source": "JCR", "year": 2025,
                                                    "entries": [{"venue": "Synthetic  journal", "tier": "Q1"}]})
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    try:
        out = library_index.list_works(conn)
    finally:
        conn.set_trace_callback(None)
    assert len(out) >= 2 and sum("settings_kv" in q for q in seen) == 1
    assert any("Q1 (JCR 2025)" in w["weight"] for w in out)
