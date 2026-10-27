"""M1 K7: import -> parse (real worker loop) -> pages/chunks/FTS; G1 anchors locate 100%; unsupported/corrupt/
encrypted/scanned clearly flagged with recovery actions; dedupe; single validated Evidence write path; stale
detection; Chinese search."""
from __future__ import annotations

import sqlite3

import pytest

from researchos.services.container import Services
from researchos.services.knowledge.text import find_span, missing_numbers

from .conftest import Client
from .gold.g1_generator import all_docs, single_column
from .m1_helpers import drain


def _upload(client: Client, name: str, data: bytes, project_id: str | None = None) -> dict:
    files = {"file": (name, data, "application/pdf")}
    r = client.post("/api/v1/documents/upload", files=files, data={"project_id": project_id} if project_id else {})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def imported(client: Client, conn: sqlite3.Connection, svc: Services) -> dict[str, dict]:
    out = {d.name: {"doc": d, **_upload(client, d.name, d.data)} for d in all_docs()}
    drain(conn, svc)
    return out


def test_g1_states_and_recovery(imported: dict[str, dict], client: Client) -> None:
    for name, x in imported.items():
        doc = client.get(f"/api/v1/documents/{x['version_id']}").json()
        assert doc["parse_state"] == x["doc"].expect_state, (name, doc["parse_state"], doc["parse_error"])
        if x["doc"].expect_error:
            assert doc["parse_error"].startswith(x["doc"].expect_error) and doc["recovery_action"]
        if doc["parse_state"] in ("partial", "needs_ocr"):
            assert doc["recovery_action"], name


def test_duplicate_bytes_reuse_version(imported: dict[str, dict]) -> None:
    assert imported["duplicate_of_single.pdf"]["created"] is False
    assert imported["duplicate_of_single.pdf"]["version_id"] == imported["single_column.pdf"]["version_id"]


def test_unsupported_type_recorded_not_skipped(client: Client) -> None:
    r = client.post("/api/v1/documents/upload", files={"file": ("notes.docx", b"PK\x03\x04 not a pdf", "x")})
    assert r.status_code == 201 and r.json()["parse_state"] == "unsupported"
    doc = client.get(f"/api/v1/documents/{r.json()['version_id']}").json()
    assert "PDF" in doc["recovery_action"]


def test_g1_anchor_locate_rate_is_100_percent(imported: dict[str, dict], client: Client) -> None:
    total = located = 0
    for x in imported.values():
        for page, sentence in x["doc"].anchors:
            total += 1
            r = client.post("/api/v1/evidence", json={"version_id": x["version_id"], "page_index": page,
                                                      "quote": sentence})
            if r.status_code == 201:
                ev = r.json()
                page_text = client.get(f"/api/v1/documents/{x['version_id']}/pages/{page}").json()["text"]
                assert page_text[ev["char_start"]:ev["char_end"]] == ev["quote"]
                located += 1
    assert total >= 10 and located == total


def test_two_column_evidence_is_unverified_not_machine_checked(imported: dict[str, dict], client: Client) -> None:
    x = imported["two_column.pdf"]
    page, sentence = x["doc"].anchors[0]
    ev = client.post("/api/v1/evidence", json={"version_id": x["version_id"], "page_index": page, "quote": sentence})
    assert ev.json()["trust"] == "unverified"  # page flagged needs_proofreading


def test_forged_quote_and_number_rules(imported: dict[str, dict], client: Client) -> None:
    vid = imported["single_column.pdf"]["version_id"]
    r = client.post("/api/v1/evidence", json={"version_id": vid, "page_index": 0,
                                              "quote": "The dataset contains recordings from 300 subjects."})
    assert r.status_code == 422 and r.json()["code"] == "quote_not_found"
    r = client.post("/api/v1/evidence", json={"version_id": vid, "page_index": 1, "extracted_value": "macro F1 = 0.95",
                                              "quote": "reached a macro F1 score of 0.91 on the held-out subjects"})
    assert r.json()["code"] == "number_not_in_quote"
    r = client.post("/api/v1/evidence", json={"version_id": vid, "page_index": 1, "extracted_value": "0.91",
                                              "quote": "reached a macro F1 score of 0.91 on the held-out subjects"})
    assert r.status_code == 201
    assert client.post("/api/v1/evidence", json={"version_id": vid, "page_index": 0, "quote": "Human"}
                       ).json()["code"] == "quote_too_short"


def test_stale_evidence_refused(imported: dict[str, dict], client: Client, conn: sqlite3.Connection) -> None:
    vid = imported["single_column.pdf"]["version_id"]
    ev = client.post("/api/v1/evidence", json={"version_id": vid, "page_index": 0,
                                               "quote": single_column().anchors[1][1]}).json()
    # rotated.pdf carries the same DOI -> same Work, newer version -> "superseded" (still citable, labelled)
    assert client.get(f"/api/v1/evidence/{ev['id']}/verify").json()["status"] == "superseded"
    conn.execute("UPDATE pages SET text=replace(text,'30 subjects','31 subjects'), text_sha256='x' "
                 "WHERE version_id=? AND page_index=0", (vid,))
    assert client.get(f"/api/v1/evidence/{ev['id']}/verify").json()["status"] == "stale"


def test_doi_identifier_and_title(imported: dict[str, dict], client: Client) -> None:
    doc = client.get(f"/api/v1/documents/{imported['single_column.pdf']['version_id']}").json()
    assert doc["doi"] == "10.5555/ros.g1.0001"
    assert doc["title"] == "Synthetic HAR study for anchor testing"


def test_search_english_and_chinese(imported: dict[str, dict], client: Client) -> None:
    hits = client.get("/api/v1/search", params={"q": "random forest macro"}).json()
    assert hits and hits[0]["page_index"] == 1 and "random forest" in hits[0]["text"]
    zh = client.get("/api/v1/search", params={"q": "惯性传感器"}).json()
    assert zh and "惯性传感器" in zh[0]["text"]
    assert client.get("/api/v1/search", params={"q": 'x" OR 1=1 --'}).status_code == 200  # no FTS injection error


def test_normalization_rules() -> None:
    assert find_span("signal pro-\ncessing works", "signal processing works") == (0, 25)
    assert find_span("ﬁnal  result", "final result") is not None
    assert missing_numbers("F1 of 0.91 with 31,000 params", "macro F1 0.91 and 31000 parameters") == []
    assert missing_numbers("accuracy 97%", "accuracy 96%") == ["97%"]


def test_qa_number_rule_equals_the_export_gate() -> None:
    """ADR-198 (found in the V1 acceptance): the PDF text had "colin2 7"; a kept answer said "colin27", which the
    QA check ignored but the final export gate refused. QA now uses the gate's prose number rule."""
    from researchos.services.knowledge.text import prose_numbers_missing

    quote = "the real head model was constructed with the help of the colin2 7 template and BEM algorithm"
    assert prose_numbers_missing("a head model built with the colin27 template", quote) == ["27"]
    assert prose_numbers_missing("accuracy 96.36% (Table 2, Figure 4A)", "averaged accuracy 96.36%") == []
    assert prose_numbers_missing("using GPT-4 and ResNet-50 in 2019 (Smith et al., 2019)", "") == []
    assert prose_numbers_missing("top-5 accuracy 0.9", "top 5 accuracy .9") == []


def test_check_answer_drops_a_sentence_the_gate_would_refuse() -> None:
    import json

    from researchos.services.research.qa_validate import check_answer

    passages = {"E1": "the real head model was constructed with the help of the colin2 7 template and BEM algorithm"}
    out = {"sentences": [{"text": "The head model used the colin27 template.", "evidence": ["E1"],
                          "kind": "source_fact"},
                         {"text": "A BEM head model was used.", "evidence": ["E1"], "kind": "source_fact"}],
           "confidence": 0.9}
    _, checked = check_answer(json.dumps(out), passages)
    assert [s["text"] for s in checked.kept] == ["A BEM head model was used."]
    assert "27" in checked.dropped[0]["reason"]
