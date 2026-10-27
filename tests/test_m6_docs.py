"""M6 gate (ARCHITECTURE §11): final exports have 0 unresolvable citations and 0 free numbers. Doc parsing,
citation/metric/evidence/number gates (draft warns, final refuses), gated export through core + plugin exporters."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from researchos.api.routers import docs as route
from researchos.contracts.errors import ApiError
from researchos.kernel import idempotency
from researchos.kernel.db import connect, tx, utcnow
from researchos.services import projects
from researchos.services.container import Services
from researchos.services.content import docs
from researchos.services.knowledge import evidence

from .fixtures.make_dataset import make
from .m2_helpers import new_plan, plan_content, run
from .m5_helpers import rec
from .test_m5_library import imported

PAGE = "In the cohort, sensitivity was 0.91 and specificity 0.84 across 120 participants."


@pytest.fixture
def lib(client: Any, svc: Services, conn: sqlite3.Connection, tmp_path: Path) -> dict[str, Any]:
    w1, w2 = imported(
        client,
        svc,
        [
            rec("fake_lib", "1", "Screening accuracy study", "10.5555/m6.1"),
            rec("fake_lib", "2", "Retracted cohort", "10.5555/m6.2"),
        ],
    )
    k1 = client.post(f"/api/v1/works/{w1}/citekey").json()["citekey"]
    k2 = client.post(f"/api/v1/works/{w2}/citekey").json()["citekey"]
    now = utcnow()
    conn.execute(
        "INSERT INTO document_versions(version_id,work_id,sha256,stored_path,original_name,size,mime,"
        "parse_state,parser_version,created_at) VALUES ('ver_m6',?,?,'raw/m6.pdf','m6.pdf',1,"
        "'application/pdf','parsed','pypdf-1',?)",
        (w1, "1" * 64, now),
    )
    conn.execute(
        "INSERT INTO pages(version_id,page_index,text,text_sha256,char_count) VALUES ('ver_m6',0,?,?,?)",
        (PAGE, hashlib.sha256(PAGE.encode()).hexdigest(), len(PAGE)),
    )
    conn.commit()
    e = evidence.create(conn, version_id="ver_m6", page_index=0, quote=PAGE, created_by="user:test")
    conn.execute("UPDATE works SET retraction=? WHERE id=?", (json.dumps({"retracted": True}), w2))
    conn.commit()
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    t = run(client, conn, svc, new_plan(client, plan_content(client, ds)))
    pid = client.post("/api/v1/projects", json={"title": "M6"}).json()["id"]
    return {"k1": k1, "k2": k2, "ev": e["id"], "run": t["run"]["id"], "pid": pid}


def doc(client: Any, lib: dict, body: str) -> str:
    r = client.post(f"/api/v1/projects/{lib['pid']}/docs", json={"kind": "manuscript", "title": "Draft", "body": body})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def codes(res: dict[str, Any]) -> list[str]:
    return sorted(d["code"] for d in res["diagnostics"])


def test_gates_final_vs_draft(client: Any, lib: dict) -> None:
    body = (
        f"# 2. Results\n\nSensitivity was 0.91 {{{{evidence:{lib['ev']}}}}} in 2024 (Figure 2), unlike GPT-4 "
        f"and {{{{lit:0.5}}}} thresholds {{{{cite:{lib['k1']}}}}}.\n\n"
        f"- Macro-F1 {{{{metric:{lib['run']}/macro_f1/search_val}}}} vs 0.77 elsewhere {{{{cite:nobody2020x}}}}\n"
    )
    did = doc(client, lib, body)
    final = client.get(f"/api/v1/docs/{did}/check", params={"mode": "final"}).json()
    assert codes(final) == ["free_number", "unresolved_citation"] and not final["ok"]
    assert final["counts"] == {"citations": 2, "metrics": 1, "evidence": 1, "free_numbers": 1}
    draft = client.get(f"/api/v1/docs/{did}/check", params={"mode": "draft"}).json()
    assert draft["ok"] and all(d["severity"] == "warning" for d in draft["diagnostics"])


def test_retracted_and_failed_evidence_and_malformed(client: Any, lib: dict, conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE evidence SET trust='failed' WHERE id=?", (lib["ev"],))
    conn.commit()
    did = doc(client, lib, f"Prior work {{{{cite:{lib['k2']}}}}} {{{{evidence:{lib['ev']}}}}} {{{{metric:bad}}}}")
    res = client.get(f"/api/v1/docs/{did}/check", params={"mode": "draft"}).json()
    assert {"retracted_citation", "evidence_failed", "malformed_placeholder"} <= set(codes(res))
    assert next(d for d in res["diagnostics"] if d["code"] == "malformed_placeholder")["severity"] == "error"


def test_final_export_refused_then_passes(client: Any, lib: dict, conn: sqlite3.Connection, settings: Any) -> None:
    did = doc(client, lib, f"Accuracy reached 0.93 {{{{cite:{lib['k1']}}}}}.")
    before = len(list((settings.data_dir / "exports").glob("*"))) if (settings.data_dir / "exports").exists() else 0
    r = client.post(f"/api/v1/docs/{did}/export", json={"exporter": "markdown", "mode": "final"})
    assert r.status_code == 409 and r.json()["code"] == "export_gates_failed"
    after = len(list((settings.data_dir / "exports").glob("*"))) if (settings.data_dir / "exports").exists() else 0
    assert after == before  # nothing written
    draft = client.post(f"/api/v1/docs/{did}/export", json={"exporter": "markdown", "mode": "draft"}).json()
    assert draft["gates"]["counts"]["free_numbers"] == 1
    v = client.get(f"/api/v1/docs/{did}").json()["version"]
    fixed = (
        f"Accuracy reached {{{{metric:{lib['run']}/macro_f1/search_val}}}} {{{{cite:{lib['k1']}}}}}; "
        f"sensitivity 0.91 {{{{evidence:{lib['ev']}}}}}."
    )
    assert client.put(f"/api/v1/docs/{did}", json={"body": fixed, "base_version": v}).status_code == 200
    ok = client.post(f"/api/v1/docs/{did}/export", json={"exporter": "markdown", "mode": "final"}).json()
    assert ok["status"] == "ok" and ok["gates"]["counts"]["free_numbers"] == 0
    assert not [d for d in ok["gates"]["diagnostics"] if d["code"] == "unresolved_citation"]
    md = (settings.data_dir / ok["files"][0]["path"]).read_text()
    assert "(recomputed)" in md and lib["k1"] in md
    assert "(PDF p. 1)" in md and "(p. 1)" not in md  # review #66: PDF page index, not the printed page label


def test_plugin_exporters_docx_latex_slides(client: Any, lib: dict, svc: Services, settings: Any) -> None:
    pytest.importorskip("researchos_plugin_report_export")
    from researchos_plugin_report_export.exporters import DocxExporter, LatexExporter
    from researchos_plugin_report_export.slides import SlidesExporter

    svc.extensions.exporters["report_docx"] = DocxExporter()
    svc.extensions.exporters["report_latex"] = LatexExporter()
    svc.extensions.exporters["report_slides"] = SlidesExporter()
    did = doc(client, lib, f"# Results\n\nF1 {{{{metric:{lib['run']}/macro_f1/search_val}}}} {{{{cite:{lib['k1']}}}}}.")
    for name, ext in (("report_docx", ".docx"), ("report_latex", ".tex"), ("report_slides", ".pptx")):
        r = client.post(f"/api/v1/docs/{did}/export", json={"exporter": name, "mode": "final"}).json()
        assert r["status"] == "ok", r
        assert any(f["path"].endswith(ext) for f in r["files"])
        broken = doc(client, lib, "Unsupported 0.42 {{cite:missing2099}}.")
        before = set((settings.data_dir / "exports").iterdir())
        refused = client.post(f"/api/v1/docs/{broken}/export", json={"exporter": name, "mode": "final"})
        assert refused.status_code == 409 and refused.json()["code"] == "export_gates_failed"
        assert set((settings.data_dir / "exports").iterdir()) == before
        draft = client.post(f"/api/v1/docs/{broken}/export", json={"exporter": name, "mode": "draft"}).json()
        assert draft["status"] == "ok" and draft["gates"]["counts"]["free_numbers"] == 1


def test_suggestion_mode_accept_reject_conflict(client: Any, lib: dict) -> None:
    did = doc(client, lib, "Intro text.\n\nOld claim here.")
    s1 = client.post(
        f"/api/v1/docs/{did}/suggestions",
        json={"op": "replace", "anchor": "Old claim here.", "text": "New claim here."},
    ).json()
    s2 = client.post(
        f"/api/v1/docs/{did}/suggestions", json={"op": "replace", "anchor": "Old claim here.", "text": "Other rewrite."}
    ).json()
    s3 = client.post(f"/api/v1/docs/{did}/suggestions", json={"op": "append", "text": "Appendix."}).json()
    assert client.get(f"/api/v1/docs/{did}").json()["body"] == "Intro text.\n\nOld claim here."  # proposals only
    r = client.post(f"/api/v1/suggestions/{s1['id']}/decision", json={"accept": True}).json()
    assert r["status"] == "accepted" and "New claim here." in r["doc"]["body"]
    assert client.post(f"/api/v1/suggestions/{s2['id']}/decision", json={"accept": True}).json()["status"] == "conflict"
    assert (
        client.post(f"/api/v1/suggestions/{s3['id']}/decision", json={"accept": False}).json()["status"] == "rejected"
    )
    assert (
        client.post(f"/api/v1/suggestions/{s1['id']}/decision", json={"accept": True}).json()["code"]
        == "already_decided"
    )


def test_cited_answer_inserted_as_gate_clean_suggestion(client: Any, lib: dict, conn: sqlite3.Connection) -> None:
    body = {
        "sentences": [
            {
                "text": "Sensitivity was 0.91 in the cohort.",
                "evidence": ["E1"],
                "evidence_ids": [lib["ev"]],
                "kind": "source_fact",
            },
            {"text": "Unsupported idea.", "kind": "hypothesis"},
        ]
    }
    conn.execute(
        "INSERT INTO answers(id,question,status,body,created_at) VALUES ('ans_m6','How accurate?','answered',?,?)",
        (json.dumps(body), utcnow()),
    )
    conn.commit()
    did = doc(client, lib, "# Draft")
    s = client.post(f"/api/v1/docs/{did}/suggestions/from-answer", json={"answer_id": "ans_m6"}).json()
    assert "{{evidence:" in s["text"] and lib["k1"] in s["text"] and "Unsupported idea" not in s["text"]
    client.post(f"/api/v1/suggestions/{s['id']}/decision", json={"accept": True})
    chk = client.get(f"/api/v1/docs/{did}/check", params={"mode": "final"}).json()
    assert chk["ok"] and chk["counts"]["free_numbers"] == 0, chk["diagnostics"]


def test_legacy_citekey_resolves_in_gate_and_export(client: Any, lib: dict, conn: sqlite3.Connection) -> None:
    """Regression (review #3): one resolver; a legacy identifiers-scheme citekey must not pass export but fail gates."""
    from researchos.services.content.citekey_index import lookup_work

    wid = conn.execute("SELECT work_id FROM citekeys WHERE citekey=?", (lib["k1"],)).fetchone()[0]
    conn.execute(
        "INSERT INTO identifiers(work_id,scheme,value,source) VALUES (?,'citekey','legacy2019key','test')", (wid,)
    )
    conn.commit()
    assert lookup_work(conn, "legacy2019key")["id"] == wid
    did = doc(client, lib, "Prior work {{cite:legacy2019key}}.")
    res = client.get(f"/api/v1/docs/{did}/check", params={"mode": "final"}).json()
    assert "unresolved_citation" not in codes(res)


def test_number_gate_exemptions_do_not_hide_data(client: Any, lib: dict) -> None:
    """Regression (review #4, ADR-119): years, design labels and model names are exempt only in label form."""
    body = "N = 2000 participants, top-5 accuracy after steps 1000; (Smith, 2019), GPT-4, Study 2, in 2021."
    res = client.get(f"/api/v1/docs/{doc(client, lib, body)}/check", params={"mode": "final"}).json()
    assert sorted(d["ref"] for d in res["diagnostics"] if d["code"] == "free_number") == ["1000", "2000", "5"]


def test_cited_markdown_is_the_single_sentence_builder(lib: dict, conn: sqlite3.Connection) -> None:
    """Regression (review #5): QA insert and deep reports share one placeholder builder."""
    import inspect

    from researchos.services.literature import citekeys
    from researchos.services.research import deep_report
    from researchos.services.writing import insert

    md = citekeys.cited_markdown(conn, "Sensitivity was 0.91.", [lib["ev"]])
    assert md == f"Sensitivity was 0.91. {{{{evidence:{lib['ev']}}}}} {{{{cite:{lib['k1']}}}}}"
    for mod in (deep_report, insert):
        assert "{{{{evidence:" not in inspect.getsource(mod) and "cited_markdown" in inspect.getsource(mod)


def test_suggestion_decision_is_an_atomic_claim(client: Any, lib: dict, conn: sqlite3.Connection) -> None:
    """Regression (review #11): a decision made concurrently (between read and write) cannot be overwritten, and a
    failed doc update releases the claim instead of leaving an 'accepted' suggestion with an unchanged doc."""
    import pytest as _pytest

    from researchos.services.writing import suggestions
    did = doc(client, lib, "Old claim here.")
    s1 = client.post(f"/api/v1/docs/{did}/suggestions", json={"op": "append", "text": "More."}).json()
    stale = suggestions.get(conn, s1["id"])
    assert (
        client.post(f"/api/v1/suggestions/{s1['id']}/decision", json={"accept": False}).json()["status"] == "rejected"
    )
    with _pytest.raises(ApiError):
        suggestions._close(conn, stale, "accepted", "user:test")  # the stale reader loses
    assert suggestions.get(conn, s1["id"])["status"] == "rejected"
    s2 = client.post(f"/api/v1/docs/{did}/suggestions", json={"op": "append", "text": "Again."}).json()
    real = docs.update

    def boom(*a: Any, **k: Any) -> Any:
        raise ApiError(409, "version_conflict", "moved on")

    docs.update = boom  # type: ignore[assignment]
    try:
        with _pytest.raises(ApiError):
            suggestions.decide(conn, s2["id"], True, "user:test")
    finally:
        docs.update = real  # type: ignore[assignment]
    assert suggestions.get(conn, s2["id"])["status"] == "proposed"
    assert client.get(f"/api/v1/docs/{did}").json()["body"].strip() == "Old claim here."


def test_doc_writes_replay_on_the_same_idempotency_key(client: Any, lib: dict) -> None:
    """Regression (U1): the web client's Idempotency-Key is honoured by doc create/update/decide/export."""
    h = {"idempotency-key": "k-create"}
    body = {"kind": "manuscript", "title": "T", "body": "Text."}
    a = client.post(f"/api/v1/projects/{lib['pid']}/docs", json=body, headers=h).json()
    b = client.post(f"/api/v1/projects/{lib['pid']}/docs", json=body, headers=h).json()
    assert a["id"] == b["id"]
    up = {"body": "Text two.", "base_version": a["version"]}
    u1 = client.put(f"/api/v1/docs/{a['id']}", json=up, headers={"idempotency-key": "k-up"})
    u2 = client.put(f"/api/v1/docs/{a['id']}", json=up, headers={"idempotency-key": "k-up"})
    assert u1.status_code == u2.status_code == 200 and u1.json()["version"] == u2.json()["version"]
    s = client.post(f"/api/v1/docs/{a['id']}/suggestions", json={"op": "append", "text": "More."}).json()
    d = [
        client.post(
            f"/api/v1/suggestions/{s['id']}/decision", json={"accept": True}, headers={"idempotency-key": "k-dec"}
        ).json()
        for _ in range(2)
    ]
    assert d[0] == d[1] and d[0]["status"] == "accepted"
    assert client.get(f"/api/v1/docs/{a['id']}").json()["body"].count("More.") == 1


def test_acceptance_interruption_rolls_back_status_revision_and_events(conn, monkeypatch):
    from researchos.services.writing import suggestions
    with tx(conn):
        pid = projects.create_in_tx(conn, 'Atomic', '', 'user:test')['id']
        did = docs.create_in_tx(conn, docs.DocKindRegistry(), pid, 'report', '', 'Original.', 'user:test')['id']
    s = suggestions.propose(conn, did, op='append', text='Added.', actor='user:test')
    before = conn.execute('SELECT count(*) FROM events').fetchone()[0]
    real = docs.update

    def interrupted(*args, **kwargs):
        real(*args, **kwargs)
        raise KeyboardInterrupt('termination after revision write')

    monkeypatch.setattr(docs, 'update', interrupted)
    with pytest.raises(KeyboardInterrupt):
        suggestions.decide(conn, s['id'], True, 'user:test')
    assert suggestions.get(conn, s['id'])['status'] == 'proposed'
    assert docs.get(conn, did)['body'] == 'Original.'
    assert docs.get(conn, did)['version'] == 1
    assert conn.execute('SELECT count(*) FROM doc_revisions WHERE doc_id=?', (did,)).fetchone()[0] == 1
    assert conn.execute('SELECT count(*) FROM events').fetchone()[0] == before


def test_concurrent_same_key_doc_create_and_export(conn, settings, svc, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    with tx(conn):
        pid = projects.create_in_tx(conn, 'Concurrent', '', 'user:test')['id']
    app = SimpleNamespace(state=SimpleNamespace(services=svc))
    barrier = Barrier(2)
    real = idempotency.lookup
    def lookup(c, scope, key, h):
        prior = real(c, scope, key, h)
        if prior is None and not c.in_transaction:
            barrier.wait(timeout=5)
        return prior
    monkeypatch.setattr(idempotency, 'lookup', lookup)
    def invoke(action):
        c = connect(settings.db_path)
        req = Request({'type': 'http', 'headers': [(b'idempotency-key', action.encode())], 'app': app})
        try:
            if action == 'create':
                return route.create_doc(pid, route.DocIn(kind='report', body='Text.'), req,
                                        SimpleNamespace(actor='user:test'), c)
            return route.export_doc(did, route.ExportIn(exporter='markdown'), req,
                                    SimpleNamespace(actor='user:test'), c)
        finally:
            c.close()
    with ThreadPoolExecutor(2) as pool:
        created = list(pool.map(invoke, ['create'] * 2))
        did = created[0]['id']
        exported = list(pool.map(invoke, ['export'] * 2))
    assert created[0]['id'] == created[1]['id']
    assert exported[0]['id'] == exported[1]['id']
    assert conn.execute('SELECT count(*) FROM docs WHERE project_id=?', (pid,)).fetchone()[0] == 1
    assert len(list((settings.data_dir / 'exports').iterdir())) == 1


def test_export_retry_after_response_record_failure_reuses_directory(conn, svc, monkeypatch):
    with tx(conn):
        pid = projects.create_in_tx(conn, 'Export', '', 'user:test')['id']
        d = docs.create_in_tx(conn, svc.doc_kinds, pid, 'report', 'Report', 'Text.', 'user:test')
    req = Request({'type': 'http', 'headers': [(b'idempotency-key', b'export-retry')],
                   'app': SimpleNamespace(state=SimpleNamespace(services=svc))})
    args = (d['id'], route.ExportIn(exporter='markdown'), req, SimpleNamespace(actor='user:test'), conn)
    real = idempotency.store
    def interrupted(*a, **kw):
        raise KeyboardInterrupt('after file publication before replay record')
    monkeypatch.setattr(idempotency, 'store', interrupted)
    with pytest.raises(KeyboardInterrupt):
        route.export_doc(*args)
    monkeypatch.setattr(idempotency, 'store', real)
    out = route.export_doc(*args)
    assert route.export_doc(*args) == out
    assert len(list((svc.settings.data_dir / 'exports').iterdir())) == 1
    assert conn.execute("SELECT count(*) FROM events WHERE kind='export.created'").fetchone()[0] == 1


def test_review_decisions_cover_missing_relay_notes():
    import re
    text = (Path(__file__).parents[1] / 'docs' / 'DECISIONS.md').read_text()
    notes = text.split('## Review fixes')[1]
    assert all(re.search(rf'\b{task}\b', notes) for task in ('T3', 'T5', 'T6', 'T8', 'T10', 'T11', 'T12'))
    assert '**ADR-129**' in notes


def test_doc_conflict_replay_keeps_one_conflict_copy(conn, svc):
    with tx(conn):
        pid = projects.create_in_tx(conn, 'Conflict', '', 'user:test')['id']
        d = docs.create_in_tx(conn, svc.doc_kinds, pid, 'report', '', 'Current', 'user:test')
    req = Request({'type': 'http', 'headers': [(b'idempotency-key', b'conflict-retry')],
                   'app': SimpleNamespace(state=SimpleNamespace(services=svc))})
    copies = []
    for _ in range(2):
        with pytest.raises(ApiError) as err:
            route.update_doc(d['id'], route.DocUpdate(body='Local edits', base_version=0), req,
                             SimpleNamespace(actor='user:test'), conn)
        copies.append(err.value.details['conflict_copy_id'])
    assert copies[0] == copies[1]
    assert docs.get(conn, copies[0])['body'] == 'Local edits'
