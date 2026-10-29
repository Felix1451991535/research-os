"""Title-based identification of PDFs without DOI/arXiv (ADR-233) on local fixture PDFs (reportlab, synthetic): the
page-1 title yields candidate SUGGESTIONS only; the file stays unidentified (`_unsorted`) until the owner confirms;
confirm links the stored document to the candidate's work, reject drops a suggestion. In-test source, offline."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from researchos.contracts.errors import ApiError
from researchos.kernel.db import connect
from researchos.services.container import Services
from researchos.services.knowledge.writer import DbKnowledgeWriter
from researchos.services.literature import identify_title

from .identify_title_helpers import TITLE_A, TITLE_B, make_lib
from .identify_title_helpers import propose as _propose
from .identify_title_helpers import second_suggestion as _second_suggestion


@pytest.fixture
def lib(tmp_path: Path, client: Any, svc: Services) -> dict[str, Any]:
    return make_lib(tmp_path, client, svc)


def test_title_guess_skips_headers_and_short_lines() -> None:
    assert identify_title.title_guess(f"Journal of X, vol. 3\nhttps://example.org\n{TITLE_A}\nbody") == TITLE_A
    assert identify_title.title_guess("Slides\nPage 1\n") is None
    assert identify_title.similarity(TITLE_A, TITLE_A.upper() + ".") == 1.0
    assert identify_title.similarity(TITLE_A, "Gut microbiome composition in mice") < identify_title.MIN_SCORE


def test_title_match_is_only_a_suggestion_until_the_owner_confirms(client: Any, conn: sqlite3.Connection,
                                                                    svc: Services, lib: dict[str, Any]) -> None:
    res = _propose(conn, svc, lib)
    files = {Path(f["src"]).name: f for f in res["files"]}
    assert lib["source"].calls == [TITLE_A, TITLE_B]  # searched by the page-1 title (not the journal header)
    for name in ("scan_0042.pdf", "workshop.pdf"):  # never silently identified
        assert files[name]["work_id"] is None and "/_unsorted/" in files[name]["target"]
    assert files["scan_0042.pdf"]["title_suggestions"] == 1 and files["workshop.pdf"]["title_suggestions"] == 0
    (sug,) = client.get("/api/v1/library/identifications").json()
    assert sug["original_name"] == "scan_0042.pdf" and sug["record"]["identifiers"] == [["doi", "10.5555/id.a"]]
    assert sug["score"] >= identify_title.MIN_SCORE and sug["title_guess"] == TITLE_A
    before = conn.execute("SELECT work_id FROM document_versions WHERE version_id=?", (sug["version_id"],)).fetchone()
    r = client.post(f"/api/v1/library/identifications/{sug['id']}/confirm")
    assert r.status_code == 200, r.text
    work = r.json()["work_id"]
    assert work != before[0]
    assert conn.execute("SELECT work_id FROM document_versions WHERE version_id=?",
                        (sug["version_id"],)).fetchone()[0] == work
    assert conn.execute("SELECT value FROM identifiers WHERE work_id=? AND scheme='doi'", (work,)).fetchone()[0] \
        == "10.5555/id.a"
    assert client.get("/api/v1/library/identifications").json() == []
    again = client.post(f"/api/v1/library/identifications/{sug['id']}/confirm")
    assert again.status_code == 409 and again.json()["code"] == "already_decided"


def test_owner_can_reject_a_suggestion(client: Any, conn: sqlite3.Connection, svc: Services,
                                       lib: dict[str, Any]) -> None:
    _propose(conn, svc, lib)
    (sug,) = client.get("/api/v1/library/identifications").json()
    assert client.post(f"/api/v1/library/identifications/{sug['id']}/reject").status_code == 204
    assert client.get("/api/v1/library/identifications").json() == []
    row = conn.execute("SELECT status, decided_by FROM doc_identifications WHERE id=?", (sug["id"],)).fetchone()
    assert row[0] == "rejected" and row[1].startswith("device:")  # the owner's session device
    assert client.post("/api/v1/library/identifications/nope/confirm").status_code == 404


@pytest.mark.parametrize("race", ["two confirms", "confirm and reject"])
def test_concurrent_decisions_on_one_document_are_serialised(client: Any, conn: sqlite3.Connection, svc: Services,
                                                             lib: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                                             race: str) -> None:
    """Review #94: two confirms of different candidates (or a confirm and a reject of one) used to both pass the
    status check before either committed; now exactly one decision wins, the other is 409, the document is linked
    to the winner's work and at most one suggestion is confirmed."""
    _propose(conn, svc, lib)
    (sug,) = client.get("/api/v1/library/identifications").json()
    second = _second_suggestion(conn, sug)
    real = DbKnowledgeWriter.upsert_work

    def slow(self: DbKnowledgeWriter, *a: Any, **k: Any) -> Any:
        time.sleep(0.3)  # widen the window between the decision and the relinking
        return real(self, *a, **k)

    monkeypatch.setattr(DbKnowledgeWriter, "upsert_work", slow)
    pair = [(sug["id"], "confirm"), (second, "confirm") if race == "two confirms" else (sug["id"], "reject")]
    gate, out = threading.Barrier(2), {}

    def decide(ident: str, action: str) -> None:
        c = connect(svc.settings.db_path)
        try:
            gate.wait()
            if action == "confirm":
                w = DbKnowledgeWriter(c, svc.blobs, svc.kinds, plugin="core.identify", actor="user:test",
                                      project_id=None, max_bytes=50_000_000)
                out[(ident, action)] = identify_title.confirm(c, w, ident, svc.settings.data_dir)
            else:
                identify_title.reject(c, ident, "user:test")
                out[(ident, action)] = "rejected"
        except ApiError as e:
            out[(ident, action)] = e.code
        finally:
            c.close()

    threads = [threading.Thread(target=decide, args=p) for p in pair]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    wins = {k: v for k, v in out.items() if v != "already_decided"}
    assert len(out) == 2 and len(wins) == 1, out
    statuses = dict(conn.execute("SELECT id, status FROM doc_identifications WHERE version_id=?",
                                 (sug["version_id"],)).fetchall())
    assert list(statuses.values()).count("confirmed") <= 1
    ((ident, action), result), = wins.items()
    linked = conn.execute("SELECT work_id FROM document_versions WHERE version_id=?", (sug["version_id"],)).fetchone()
    if action == "confirm":
        assert statuses == {ident: "confirmed", **{i: "rejected" for i in statuses if i != ident}}
        assert linked[0] == result
    else:
        assert statuses == {sug["id"]: "rejected", second: "suggested"}


def test_a_confirm_that_fails_while_linking_reopens_the_suggestions(client: Any, conn: sqlite3.Connection,
                                                                    svc: Services, lib: dict[str, Any],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    _propose(conn, svc, lib)
    (sug,) = client.get("/api/v1/library/identifications").json()
    second = _second_suggestion(conn, sug)

    def boom(self: DbKnowledgeWriter, *a: Any, **k: Any) -> Any:
        raise OSError("disk gone")

    monkeypatch.setattr(DbKnowledgeWriter, "attach_original", boom)
    w = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.identify", actor="user:test", project_id=None,
                          max_bytes=50_000_000)
    with pytest.raises(OSError):
        identify_title.confirm(conn, w, sug["id"], svc.settings.data_dir)
    assert {s["id"] for s in identify_title.pending(conn)} == {sug["id"], second}


def test_a_failed_confirm_reopens_only_its_own_decision(client: Any, conn: sqlite3.Connection, svc: Services,
                                                        lib: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Review #100: the rollback matched suggestions by their decision timestamp, so a separate rejection taken in
    the same millisecond was re-opened as well. Fixed clock: reject A, confirm B (linking fails) -> A stays."""
    _propose(conn, svc, lib)
    (sug,) = client.get("/api/v1/library/identifications").json()
    second = _second_suggestion(conn, sug)
    monkeypatch.setattr(identify_title, "utcnow", lambda: "2026-10-28T00:00:00.000Z")
    identify_title.reject(conn, second, "user:test")  # A, an independent decision

    def boom(self: DbKnowledgeWriter, *a: Any, **k: Any) -> Any:
        raise OSError("disk gone")

    monkeypatch.setattr(DbKnowledgeWriter, "attach_original", boom)
    w = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.identify", actor="user:test", project_id=None,
                          max_bytes=50_000_000)
    with pytest.raises(OSError):
        identify_title.confirm(conn, w, sug["id"], svc.settings.data_dir)  # B, same timestamp
    statuses = dict(conn.execute("SELECT id, status FROM doc_identifications WHERE version_id=?",
                                 (sug["version_id"],)).fetchall())
    assert statuses == {sug["id"]: "suggested", second: "rejected"}


def test_a_confirm_interrupted_after_the_decision_can_be_finished(client: Any, conn: sqlite3.Connection,
                                                                  svc: Services, lib: dict[str, Any]) -> None:
    """Review #99: the decision was committed before the linking, so a process that ended in between left the
    suggestion confirmed and its siblings rejected with the document unidentified, and every retry got 409. A
    confirmed suggestion is final only once linked; until then it stays in the queue and a confirm finishes it."""
    _propose(conn, svc, lib)
    (sug,) = client.get("/api/v1/library/identifications").json()
    second = _second_suggestion(conn, sug)
    identify_title._decide(conn, sug["id"], "user:test", "confirmed")  # committed; the process ends here
    c = connect(svc.settings.db_path)  # a fresh connection after the restart: nothing kept in memory
    try:
        (queued,) = identify_title.pending(c)
        assert queued["id"] == sug["id"] and queued["status"] == "confirmed"
        r = client.post(f"/api/v1/library/identifications/{sug['id']}/confirm")
        assert r.status_code == 200, r.text
        assert c.execute("SELECT work_id FROM document_versions WHERE version_id=?",
                         (sug["version_id"],)).fetchone()[0] == r.json()["work_id"]
        statuses = dict(c.execute("SELECT id, status FROM doc_identifications WHERE version_id=?",
                                  (sug["version_id"],)).fetchall())
        assert statuses == {sug["id"]: "confirmed", second: "rejected"}
        assert client.get("/api/v1/library/identifications").json() == []
        again = client.post(f"/api/v1/library/identifications/{sug['id']}/confirm")
        assert again.status_code == 409 and again.json()["code"] == "already_decided"
        assert c.execute("SELECT count(*) FROM events WHERE kind='library.identified'").fetchone()[0] == 1
    finally:
        c.close()
