"""Title identification under overlapping and interrupted confirms (Review #101-#104): attempts that overlap, a stale
attempt after another confirm linked the document, and confirms left from before decision ids (core 0017)."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from researchos.contracts.errors import ApiError
from researchos.kernel import migrate
from researchos.kernel.db import connect, tx
from researchos.services.container import Services
from researchos.services.knowledge.writer import DbKnowledgeWriter
from researchos.services.literature import identify_title

from .identify_title_helpers import make_lib
from .identify_title_helpers import propose as _propose
from .identify_title_helpers import second_suggestion as _second_suggestion


@pytest.fixture
def lib(tmp_path: Path, client: Any, svc: Services) -> dict[str, Any]:
    return make_lib(tmp_path, client, svc)


@pytest.mark.parametrize("failing", ["first attempt", "retry"])
def test_overlapping_confirms_where_one_fails_keep_the_winner(client: Any, conn: sqlite3.Connection, svc: Services,
                                                              lib: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                                              failing: str) -> None:
    """Review #101: a confirm and its retry overlap (same decision); one fails while the other is still linking.
    The failure re-opened the suggestion and its siblings, and the other then set `linked_at` on a suggestion that
    was `suggested` again. Now the failure re-opens only while it owns the linking (attempt id), and a success
    after a re-open takes the decision again: the winner ends confirmed and linked, the sibling rejected."""
    _propose(conn, svc, lib)
    (sug,) = client.get("/api/v1/library/identifications").json()
    second = _second_suggestion(conn, sug)
    real = DbKnowledgeWriter.attach_original
    first_linking, first_done, seen = threading.Event(), threading.Event(), {}

    def statuses() -> dict[str, str]:
        c = connect(svc.settings.db_path)
        try:
            return dict(c.execute("SELECT id, status FROM doc_identifications WHERE version_id=?",
                                  (sug["version_id"],)).fetchall())
        finally:
            c.close()

    def attach(self: DbKnowledgeWriter, *a: Any, **k: Any) -> Any:
        if threading.current_thread().name == "first":
            first_linking.set()
            if failing == "first attempt":
                time.sleep(0.2)  # the retry has taken the linking over by now
                raise OSError("disk gone")
            assert first_done.wait(10)  # the retry fails while this attempt is still linking
            return real(self, *a, **k)
        if failing == "retry":
            raise OSError("disk gone")
        assert first_done.wait(10)  # the first attempt has failed; this one is still linking
        seen["while linking"] = statuses()
        return real(self, *a, **k)

    monkeypatch.setattr(DbKnowledgeWriter, "attach_original", attach)
    out: dict[str, Any] = {}

    def run(name: str) -> None:
        c = connect(svc.settings.db_path)
        try:
            w = DbKnowledgeWriter(c, svc.blobs, svc.kinds, plugin="core.identify", actor="user:test",
                                  project_id=None, max_bytes=50_000_000)
            out[name] = identify_title.confirm(c, w, sug["id"], svc.settings.data_dir)
        except OSError as e:
            out[name] = e
        finally:
            c.close()
            if name == ("first" if failing == "first attempt" else "retry"):
                first_done.set()

    first = threading.Thread(target=run, args=("first",), name="first")
    first.start()
    assert first_linking.wait(10)
    run("retry")  # the retry of the same confirm (e.g. the owner clicked again), on this thread
    first.join(10)
    winner = "retry" if failing == "first attempt" else "first"
    loser = "first" if winner == "retry" else "retry"
    assert isinstance(out[loser], OSError) and isinstance(out[winner], str), out
    if failing == "first attempt":  # the failed first attempt no longer owned the linking: nothing re-opened
        assert seen["while linking"] == {sug["id"]: "confirmed", second: "rejected"}
    assert statuses() == {sug["id"]: "confirmed", second: "rejected"}
    r = conn.execute("SELECT linked_at FROM doc_identifications WHERE id=?", (sug["id"],)).fetchone()
    assert r[0] is not None
    assert conn.execute("SELECT work_id FROM document_versions WHERE version_id=?",
                        (sug["version_id"],)).fetchone()[0] == out[winner]
    assert client.get("/api/v1/library/identifications").json() == []
    assert conn.execute("SELECT count(*) FROM events WHERE kind='library.identified'").fetchone()[0] == 1


def test_a_stale_confirm_cannot_relink_a_document_another_confirm_linked(
        client: Any, conn: sqlite3.Connection, svc: Services, lib: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Review #103: attempt A pauses before attaching; its retry fails and re-opens the suggestions; the owner
    confirms the other candidate B, which links; A resumes. The ownership check ran after A's relink, so A got 409
    but the document pointed to A's work while B stayed confirmed. Now the check and the relink are one
    transaction: A gets 409 and B's link, decision and event stand."""
    _propose(conn, svc, lib)
    (sug,) = client.get("/api/v1/library/identifications").json()
    second = _second_suggestion(conn, sug)
    real = DbKnowledgeWriter.attach_original
    a_paused, resume_a = threading.Event(), threading.Event()
    main_calls: list[str] = []

    def attach(self: DbKnowledgeWriter, *a: Any, **k: Any) -> Any:
        if threading.current_thread().name == "A":
            a_paused.set()
            assert resume_a.wait(10)
            return real(self, *a, **k)
        main_calls.append("call")
        if len(main_calls) == 1:  # the retry of A's confirm
            raise OSError("disk gone")
        return real(self, *a, **k)  # the owner's confirm of B

    monkeypatch.setattr(DbKnowledgeWriter, "attach_original", attach)
    out: dict[str, Any] = {}

    def run_a() -> None:
        c = connect(svc.settings.db_path)
        try:
            w = DbKnowledgeWriter(c, svc.blobs, svc.kinds, plugin="core.identify", actor="user:test",
                                  project_id=None, max_bytes=50_000_000)
            out["A"] = identify_title.confirm(c, w, sug["id"], svc.settings.data_dir)
        except ApiError as e:
            out["A"] = e
        finally:
            c.close()

    a = threading.Thread(target=run_a, name="A")
    a.start()
    assert a_paused.wait(10)
    w = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.identify", actor="user:test", project_id=None,
                          max_bytes=50_000_000)
    with pytest.raises(OSError):
        identify_title.confirm(conn, w, sug["id"], svc.settings.data_dir)  # the retry fails and re-opens
    b_work = identify_title.confirm(conn, w, second, svc.settings.data_dir)  # the owner confirms B
    resume_a.set()
    a.join(10)
    assert isinstance(out["A"], ApiError) and out["A"].status == 409, out
    assert conn.execute("SELECT work_id FROM document_versions WHERE version_id=?",
                        (sug["version_id"],)).fetchone()[0] == b_work
    assert conn.execute("SELECT 1 FROM works WHERE id=?", (b_work,)).fetchone() is not None
    statuses = dict(conn.execute("SELECT id, status FROM doc_identifications WHERE version_id=?",
                                 (sug["version_id"],)).fetchall())
    assert statuses == {sug["id"]: "rejected", second: "confirmed"}
    assert conn.execute("SELECT linked_at FROM doc_identifications WHERE id=?", (second,)).fetchone()[0] is not None
    (ev,) = conn.execute("SELECT data FROM events WHERE kind='library.identified'").fetchall()
    assert b_work in ev[0]
    assert client.get("/api/v1/library/identifications").json() == []


def test_a_confirm_interrupted_before_decision_ids_can_be_rejected_after_a_failed_retry(
        client: Any, conn: sqlite3.Connection, svc: Services, lib: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Review #102: a confirm interrupted before core 0014 kept `decision_id` NULL; 0015 made it retryable, but a
    failed retry ran `_undo(NULL)`, which matched nothing: the suggestion stayed confirmed and reject was 409. Core
    0017 gives such confirms a decision id. Review #104: it no longer gives the same id to rejections with the
    confirm's timestamp and actor - a separate rejection in the same millisecond looks alike - so every legacy
    rejection, the confirm's own sibling included, stays rejected."""
    _propose(conn, svc, lib)
    (sug,) = client.get("/api/v1/library/identifications").json()
    second = _second_suggestion(conn, sug)
    other = conn.execute("SELECT id FROM lit_candidates WHERE id NOT IN (?, (SELECT candidate_id FROM "
                         "doc_identifications WHERE id=?)) ORDER BY id LIMIT 1", (sug["candidate_id"], second)
                         ).fetchone()[0]
    # the state a pre-0014 confirm left when the process ended before linking: the suggestion confirmed and its
    # sibling rejected with one decided_at / decided_by, no decision id; a third rejected on its own by the same
    # actor in the same millisecond (indistinguishable from the sibling)
    same = "2026-10-02T00:00:00.000Z"
    conn.execute("INSERT INTO doc_identifications(id, version_id, candidate_id, title_guess, score, status, "
                 "decided_by, decided_at, created_at) VALUES ('idn_third', ?, ?, ?, 0.88, 'rejected', 'user:test', "
                 "?, ?)", (sug["version_id"], other, sug["title_guess"], same, sug["created_at"]))
    conn.execute("UPDATE doc_identifications SET status=CASE id WHEN ? THEN 'confirmed' ELSE 'rejected' END, "
                 "decided_by='user:test', decided_at=?, decision_id=NULL, linked_at=NULL "
                 "WHERE id IN (?, ?)", (sug["id"], same, sug["id"], second))
    conn.commit()
    sql = (Path(migrate.CORE_DIR) / "0017_doc_identification_legacy_decisions.sql").read_text(encoding="utf-8")
    with tx(conn):  # the upgrade
        for stmt in migrate.split_sql(sql):
            conn.execute(stmt)
    ids = dict(conn.execute("SELECT id, decision_id FROM doc_identifications WHERE version_id=?",
                            (sug["version_id"],)).fetchall())
    assert ids == {sug["id"]: f"dec_legacy_{sug['id']}", second: None, "idn_third": None}

    def boom(self: DbKnowledgeWriter, *a: Any, **k: Any) -> Any:
        raise OSError("disk gone")

    monkeypatch.setattr(DbKnowledgeWriter, "attach_original", boom)
    w = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.identify", actor="user:test", project_id=None,
                          max_bytes=50_000_000)
    with pytest.raises(OSError):
        identify_title.confirm(conn, w, sug["id"], svc.settings.data_dir)  # the resumed linking fails
    statuses = dict(conn.execute("SELECT id, status FROM doc_identifications WHERE version_id=?",
                                 (sug["version_id"],)).fetchall())
    assert statuses == {sug["id"]: "suggested", second: "rejected", "idn_third": "rejected"}
    monkeypatch.undo()
    assert client.post(f"/api/v1/library/identifications/{sug['id']}/reject").status_code == 204
    assert client.post(f"/api/v1/library/identifications/{second}/confirm").status_code == 409
    assert client.post("/api/v1/library/identifications/idn_third/confirm").status_code == 409
