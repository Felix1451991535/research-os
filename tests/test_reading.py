"""Research-method feature 2: captions first + author main-point sentences as quote-verified Evidence (page +
offsets), deterministic cue list, references excluded, idempotent. Real parser on a generated PDF; no model."""

from __future__ import annotations

import sqlite3
from io import BytesIO
from typing import Any

from researchos.services.container import Services
from researchos.services.knowledge import evidence

from .m1_helpers import drain

LINES = ["Introduction",
         "Wearable sensing is common. We propose a lightweight network for activity recognition.",
         "In contrast with previous work, we evaluate on held-out subjects only.",
         "Figure 1. Accuracy of the model across subjects.",
         "Results",
         "The data suggest that window length matters more than depth.",
         "Table 2. Macro-F1 by window length for each model.",
         "References",
         "Smith A. We propose many things. Journal of Examples."]


def _pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    y = 760
    for line in LINES:
        c.drawString(60, y, line)
        y -= 40  # paragraph gaps -> separate chunks
    c.showPage()
    c.save()
    return buf.getvalue()


def test_captions_first_then_main_points_as_verified_evidence(client: Any, conn: sqlite3.Connection,
                                                              svc: Services) -> None:
    vid = client.post("/api/v1/documents/upload", files={"file": ("p.pdf", _pdf(), "application/pdf")}).json()[
        "version_id"]
    drain(conn, svc)
    r = client.post(f"/api/v1/documents/{vid}/reading").json()
    assert r["new"] == {"captions": 0, "signals": 0}  # already extracted automatically by the parse task
    assert [c["quote"] for c in r["captions"]] == ["Figure 1. Accuracy of the model across subjects.",
                                                   "Table 2. Macro-F1 by window length for each model."]
    cues = {m["cue"]: m["quote"] for m in r["main_points"]}
    assert set(cues) == {"we_propose", "contrast_prior", "data_suggest"}  # the reference entry is excluded
    assert cues["we_propose"] == "We propose a lightweight network for activity recognition."
    assert all(m["kind"] == "author_conclusion" for m in r["main_points"])
    assert all(c["kind"] == "source_fact" for c in r["captions"])
    page = conn.execute("SELECT text FROM pages WHERE version_id=? AND page_index=0", (vid,)).fetchone()[0]
    for item in r["captions"] + r["main_points"]:
        assert page[item["char_start"]:item["char_end"]] == item["quote"]  # bound to exact source location
        assert evidence.verify(conn, item["id"]).status == "ok"
    again = client.post(f"/api/v1/documents/{vid}/reading").json()
    assert again["new"] == {"captions": 0, "signals": 0} and len(again["main_points"]) == 3  # idempotent


def test_paper_card_fields_cited_from_this_work_only(client: Any, conn: sqlite3.Connection, svc: Services) -> None:
    import json

    from researchos.contracts.gateway import ChatRequest
    from researchos.services.gateway import catalog, certification
    from researchos.services.research.qa_prompts import VERIFY_SUPPORT

    from .m1_helpers import configure_models

    def respond(req: ChatRequest) -> str:
        system = req.messages[0].content
        if "check whether each claim" in system:
            return json.dumps({"results": [{"id": "S1", "supported": True}]})
        return json.dumps({"sentences": [{"text": "The authors build a compact model for activity recognition.",
                                          "evidence": ["E1"], "kind": "author_conclusion"}], "confidence": 0.9})

    for p in svc.gateway.providers.values():
        p.responder = respond  # type: ignore[attr-defined]
    configure_models(conn, svc)
    for a in catalog.list_all(conn):
        certification.record(conn, "test", a["model_id"], VERIFY_SUPPORT.hash,
                             {"locate_rate": 1, "support_precision": 1, "refusal_rate": 1})
    pid = client.post("/api/v1/projects", json={"title": "Cards"}).json()["id"]
    up = [client.post("/api/v1/documents/upload", data={"project_id": pid},
                      files={"file": (n, b, "application/pdf")}).json()["version_id"]
          for n, b in (("p.pdf", _pdf()), ("other.pdf", _pdf_other()))]
    drain(conn, svc)
    work = conn.execute("SELECT work_id FROM document_versions WHERE version_id=?", (up[0],)).fetchone()[0]
    r = client.post(f"/api/v1/works/{work}/card", json={"project_id": pid}, headers={"idempotency-key": "card-1"})
    assert r.status_code == 201, r.text
    card = r.json()
    n_answers = conn.execute("SELECT count(*) FROM answers").fetchone()[0]
    again = client.post(f"/api/v1/works/{work}/card", json={"project_id": pid}, headers={"idempotency-key": "card-1"})
    assert again.json()["doc"]["id"] == card["doc"]["id"]  # U3: a retry replays, no second card or paid QA calls
    assert conn.execute("SELECT count(*) FROM answers").fetchone()[0] == n_answers
    for s in client.get(f"/api/v1/docs/{card['doc']['id']}/suggestions", params={"status": "proposed"}).json():
        assert client.post(f"/api/v1/suggestions/{s['id']}/decision", json={"accept": True}).json()["status"] == \
            "accepted"
    body = client.get(f"/api/v1/docs/{card['doc']['id']}").json()["body"]
    assert "_(pending" not in body and "## Relation to my work\n\n_(owner)_" in body  # owner field untouched
    ev_ids = [x.split("}}")[0] for x in body.split("{{evidence:")[1:]]
    works = {conn.execute("SELECT v.work_id FROM evidence e JOIN document_versions v ON v.version_id=e.version_id "
                          "WHERE e.id=?", (e,)).fetchone()[0] for e in ev_ids}
    assert ev_ids and works == {work}  # every cited statement comes from this paper's full text
    assert "(we_propose)" in body  # author signal sentences from feature 2
    chk = client.get(f"/api/v1/docs/{card['doc']['id']}/check", params={"mode": "final"}).json()
    assert chk["ok"], chk["diagnostics"]


def _pdf_other() -> bytes:
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    c.drawString(60, 760, "An unrelated paper about activity recognition models and soil chemistry.")
    c.showPage()
    c.save()
    return buf.getvalue()


def test_lost_cue_relation_is_repaired_not_duplicated(client: Any, conn: sqlite3.Connection, svc: Services) -> None:
    """Regression (review #6): Evidence stored but its reading_cue relation lost (crash between the two writes)."""
    vid = client.post("/api/v1/documents/upload", files={"file": ("p.pdf", _pdf(), "application/pdf")}).json()[
        "version_id"]
    drain(conn, svc)
    n = conn.execute("SELECT count(*) FROM evidence WHERE version_id=?", (vid,)).fetchone()[0]
    conn.execute("DELETE FROM relations WHERE type='reading_cue'")
    conn.commit()
    r = client.post(f"/api/v1/documents/{vid}/reading").json()
    assert r["new"] == {"captions": 0, "signals": 0} and len(r["main_points"]) == 3
    assert conn.execute("SELECT count(*) FROM evidence WHERE version_id=?", (vid,)).fetchone()[0] == n
    attrs = conn.execute("SELECT attrs FROM relations WHERE type='reading_cue' LIMIT 1").fetchone()[0]
    assert attrs.startswith("{") and "reading-cues@" in attrs


def test_caption_without_period_does_not_swallow_next_paragraph() -> None:
    """Regression (review #10): an unterminated caption ends at the blank line."""
    from researchos.services.knowledge.pdf import _chunk_spans

    text = "Figure 3 Accuracy per subject\n\nWe train the model for longer.\nIt then converges on every subject.\n"
    chunks = [text[a:b].strip() for a, b in _chunk_spans(text)]
    assert chunks[0] == "Figure 3 Accuracy per subject"
    assert chunks[1].startswith("We train") and chunks[1].endswith("every subject.")


def test_reparse_upgrades_documents_from_an_older_parser(client: Any, conn: sqlite3.Connection,
                                                         svc: Services) -> None:
    """Regression (U4): docs parsed by an older parser (e.g. before caption chunks) had no path to the new chunks."""
    from researchos.services.knowledge.pdf import PARSER_VERSION

    vid = client.post("/api/v1/documents/upload", files={"file": ("p.pdf", _pdf(), "application/pdf")}).json()[
        "version_id"]
    drain(conn, svc)
    conn.execute("DELETE FROM relations WHERE type='reading_cue'")
    conn.execute("DELETE FROM evidence WHERE version_id=?", (vid,))
    conn.execute(
        "UPDATE chunks SET parser_version='pypdf-text/1', chunk_id=chunk_id || '-v1' WHERE version_id=?", (vid,)
    )
    conn.execute("UPDATE document_versions SET parser_version='pypdf-text/1' WHERE version_id=?", (vid,))
    conn.execute("UPDATE tasks SET idempotency_key=? WHERE idempotency_key=?", (f"parse:{vid}:pypdf-text/1",
                                                                                 f"parse:{vid}:{PARSER_VERSION}"))
    conn.commit()
    r = client.post(f"/api/v1/documents/{vid}/reparse")
    assert r.status_code == 202 and r.json()["parser_version"] == "pypdf-text/1"
    drain(conn, svc)
    pv = conn.execute("SELECT parser_version FROM document_versions WHERE version_id=?", (vid,)).fetchone()[0]
    assert pv == PARSER_VERSION
    listing = client.get(f"/api/v1/documents/{vid}/reading").json()
    assert len(listing["captions"]) == 2 and len(listing["main_points"]) == 3
    again = client.post(f"/api/v1/documents/{vid}/reparse").json()
    assert again["task_id"] == r.json()["task_id"]  # same parser version -> same task


def test_concurrent_reading_extraction_is_atomic(conn, settings, monkeypatch):
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from researchos.kernel.db import connect, utcnow
    from researchos.services.knowledge import reading
    vid, text = "ver_atomic", "Figure 1. Accuracy across subjects."
    now = utcnow()
    digest = hashlib.sha256(text.encode()).hexdigest()
    conn.execute("INSERT INTO works(id,title,created_at,updated_at) VALUES ('work_atomic','Paper',?,?)", (now, now))
    conn.execute("INSERT INTO document_versions(version_id,work_id,sha256,stored_path,original_name,size,mime,"
                 "parse_state,parser_version,created_at) VALUES (?,'work_atomic',?,'p.pdf','p.pdf',1,"
                 "'application/pdf','parsed','test',?)", (vid, digest, now))
    conn.execute("INSERT INTO pages(version_id,page_index,text,text_sha256,char_count) VALUES (?,0,?,?,?)",
                 (vid, text, digest, len(text)))
    conn.execute("INSERT INTO chunks(chunk_id,version_id,parser_version,page_index,char_start,char_end,"
                 "chunk_kind,text,text_sha256,seq) VALUES ('chunk_atomic',?,'test',0,0,?,'caption',?,?,0)",
                 (vid, len(text), text, digest))
    barrier = Barrier(2)
    real = reading.row

    def synchronized_read(cur):
        result = real(cur)
        # Force both old implementations to finish the unlocked dedup lookup before either inserts.
        if cur.description and [c[0] for c in cur.description] == ["id"] and not cur.connection.in_transaction:
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(reading, "row", synchronized_read)
    def extract():
        c = connect(settings.db_path)
        try:
            return reading.extract(c, vid)
        finally:
            c.close()

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: extract(), range(2)))
    assert sum(r["captions"] + r["signals"] for r in results) == 1
    assert conn.execute("SELECT count(*) FROM evidence WHERE version_id=?", (vid,)).fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM relations WHERE type='reading_cue'").fetchone()[0] == 1


def test_card_retry_resumes_after_answer_before_suggestion(conn, svc, monkeypatch):
    import json
    from types import SimpleNamespace

    import pytest
    from starlette.requests import Request

    from researchos.api.routers import docs as route
    from researchos.kernel.db import tx, utcnow
    from researchos.services import projects
    from researchos.services.writing import paper_card

    now = utcnow()
    with tx(conn):
        pid = projects.create_in_tx(conn, 'Cards', '', 'user:test')['id']
        conn.execute("INSERT INTO works(id,title,created_at,updated_at) VALUES ('w_card','Paper',?,?)", (now, now))
        conn.execute("INSERT INTO document_versions(version_id,work_id,sha256,stored_path,original_name,size,mime,"
                     "parse_state,parser_version,created_at) VALUES ('v_card','w_card',?,'p','p',1,'application/pdf',"
                     "'parsed','test',?)", ('c' * 64, now))
    calls = []
    def answer(c, gw, ctx):
        aid = f'answer_{len(calls)}'
        calls.append(ctx.question)
        c.execute("INSERT INTO answers(id,project_id,question,status,body,trace_id,created_at) VALUES (?,?,?,"
                  "'insufficient_material',?,?,?)", (aid, pid, ctx.question, json.dumps({'sentences': []}),
                                                    ctx.trace_id, utcnow()))
        return {'id': aid, 'status': 'insufficient_material'}
    monkeypatch.setattr(paper_card.qa, 'answer', answer)
    real = paper_card.suggestions.propose
    def interrupt(*a, **kw):
        raise RuntimeError('failure after saved answer')
    monkeypatch.setattr(paper_card.suggestions, 'propose', interrupt)
    req = Request({'type': 'http', 'headers': [(b'idempotency-key', b'card-retry')],
                   'app': SimpleNamespace(state=SimpleNamespace(services=svc))})
    args = ('w_card', route.CardIn(project_id=pid), req, SimpleNamespace(actor='user:test'), conn)
    with pytest.raises(RuntimeError):
        route.paper_card_create(*args)
    monkeypatch.setattr(paper_card.suggestions, 'propose', real)
    card = route.paper_card_create(*args)
    assert len(calls) == len(paper_card.FIELDS)
    assert conn.execute("SELECT count(*) FROM docs WHERE kind='paper_card'").fetchone()[0] == 1
    assert len(card['fields']) == 5
    assert route.paper_card_create(*args) == card
    # A second connection cannot execute an in-flight card with the same key.
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from researchos.contracts.errors import ApiError
    from researchos.kernel.db import connect
    started, release = Event(), Event()
    def delayed(c, gw, ctx):
        started.set()
        assert release.wait(5)
        return answer(c, gw, ctx)
    monkeypatch.setattr(paper_card.qa, 'answer', delayed)
    req2 = Request({'type': 'http', 'headers': [(b'idempotency-key', b'card-concurrent')], 'app': req.app})
    def run_card():
        c = connect(svc.settings.db_path)
        try:
            return route.paper_card_create('w_card', route.CardIn(project_id=pid), req2,
                                           SimpleNamespace(actor='user:test'), c)
        finally:
            c.close()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(run_card)
        assert started.wait(5)
        try:
            with pytest.raises(ApiError) as exc:
                route.paper_card_create('w_card', route.CardIn(project_id=pid), req2,
                                        SimpleNamespace(actor='user:test'), conn)
            assert exc.value.code == 'operation_in_progress'
        finally:
            release.set()
        concurrent = future.result(timeout=5)
    assert len(calls) == 2 * len(paper_card.FIELDS)
    assert route.paper_card_create('w_card', route.CardIn(project_id=pid), req2,
                                  SimpleNamespace(actor='user:test'), conn) == concurrent
