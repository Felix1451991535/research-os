"""M5 gate (ARCHITECTURE §11): 30-PDF fixture -> correct organisation plan; crash recovery; undo restores identical
hashes. Plus K4 safety: roots, approval, never-overwrite, locked-file pause. Synthetic files only, offline."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from researchos.platform import sysinfo
from researchos.services.container import Services
from researchos.services.effects import apply as eff
from researchos.services.effects import fsops, journal
from researchos.services.knowledge.writer import DbKnowledgeWriter
from researchos.services.literature import organize
from researchos.services.literature.discovery import Ctx

from .m1_helpers import drain
from .m2_helpers import approve
from .m5_helpers import FakeSource, register
from .m5_library_fixture import build, tree


class Crash(BaseException):
    """Simulated process death (not an Exception: nothing may catch it)."""


@pytest.fixture
def lib(tmp_path: Path, client: Any, svc: Services, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    trash_dir = tmp_path / "trash"  # stand-in for the OS recycle bin (this sandbox mounts every /tmp file)
    trash_dir.mkdir()
    monkeypatch.setattr(sysinfo, "move_to_trash", lambda p: p.rename(trash_dir / f"{uuid.uuid4().hex}-{p.name}"))
    fx = build(tmp_path)
    register(svc, FakeSource("fake_lib", hits=fx["hits"]))  # type: ignore[arg-type]
    library = tmp_path / "Library"
    library.mkdir()
    r = client.put("/api/v1/settings/file-roots", json={"roots": [{"path": str(fx["src"]), "access": "write"},
                                                                  {"path": str(library), "access": "write"}]})
    assert r.status_code == 200, r.text
    return {**fx, "library": library}


def propose(conn: sqlite3.Connection, svc: Services, lib: dict[str, Any], mode: str = "copy",
            library: Path | None = None) -> dict[str, Any]:
    writer = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.library", actor="user:test",
                               project_id=None, max_bytes=50_000_000)
    return organize.propose(conn, Ctx(svc.extensions, svc.keystore, "user:test"), writer,
                            svc.settings.data_dir / "home", source_dir=str(lib["src"]),
                            library_dir=str(library or lib["library"]), mode=mode)


def test_gate_correct_plan_for_30_pdfs(client: Any, conn: sqlite3.Connection, svc: Services, lib: dict) -> None:
    before = tree(lib["src"])
    res = propose(conn, svc, lib)
    targets = {Path(f["src"]).name: Path(f["target"]).relative_to(lib["library"]).as_posix() for f in res["files"]}
    assert targets == lib["expect"]
    assert len(res["files"]) == 28 and any("readme.txt" in n for n in res["notes"])
    assert sum("duplicate bytes" in n for n in res["notes"]) == 2
    p = res["plan"]
    assert p["diff"]["counts"] == {"mkdir": 7, "copy": 28} and p["state"] == "proposed"  # 2015-2019, 2024, _unsorted
    assert tree(lib["src"]) == before and tree(lib["library"]) == {}  # dry run: nothing changed outside data_dir
    assert conn.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0] == 28  # CAS import (internal)
    r = client.post(f"/api/v1/effects/{p['id']}/apply")
    assert r.json()["code"] == "effect_not_approved"
    approve(client, "effect_plan", p["id"])
    assert client.post(f"/api/v1/effects/{p['id']}/apply").status_code == 202
    drain(conn, svc, max_tasks=40)
    assert client.get(f"/api/v1/effects/{p['id']}").json()["state"] == "applied"
    out = tree(lib["library"])
    for f in res["files"]:
        rel = Path(f["target"]).relative_to(lib["library"]).as_posix()
        assert out[rel] == before[Path(f["src"]).relative_to(lib["src"]).as_posix()]
    assert tree(lib["src"]) == before  # copy-by-default: originals untouched


def test_gate_crash_recovery_and_undo_restores_hashes(client: Any, conn: sqlite3.Connection, svc: Services,
                                                      lib: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    src_before = tree(lib["src"])
    p = propose(conn, svc, lib, mode="move")["plan"]
    approve(client, "effect_plan", p["id"])
    real_publish, real_write, calls = fsops._publish, journal.write, {"n": 0}

    def crash_mid_copy(tmp: Path, dst: Path) -> None:  # not used by same-volume moves; kept for cross-volume
        real_publish(tmp, dst)

    def crash_before_done(conn_: Any, pid: str, i: int, phase: str, actor: str, detail: Any = None) -> None:
        if phase == "done":
            calls["n"] += 1
            if calls["n"] == 12:
                raise Crash()  # the move happened, the journal never heard of it
        real_write(conn_, pid, i, phase, actor, detail)

    monkeypatch.setattr(journal, "write", crash_before_done)
    monkeypatch.setattr(fsops, "_publish", crash_mid_copy)
    with pytest.raises(Crash):
        eff.apply(conn, p["id"], "user:test")
    monkeypatch.setattr(journal, "write", real_write)
    view = client.get(f"/api/v1/effects/{p['id']}").json()
    assert view["state"] == "applying" and view["needs_recovery"]
    assert eff.apply(conn, p["id"], "user:test")["state"] == "applied"  # owner resumes: recover, then continue
    phases = [e["phase"] for e in journal.entries(conn, p["id"])]
    assert "recovered" in phases and phases.count("done") + phases.count("recovered") == len(p["ops"])
    assert not list(lib["library"].rglob("*.ros-partial")) and not list(lib["src"].rglob("*.ros-partial"))
    moved = tree(lib["library"])
    assert sum(1 for v in moved.values() if v != "dir") == 28
    assert eff.undo(conn, p["id"], "user:test") == {"state": "undone", "conflicts": []}
    assert tree(lib["src"]) == src_before and tree(lib["library"]) == {}  # identical hashes, folders removed


def test_crash_after_partial_copy_is_cleaned_and_retried(client: Any, conn: sqlite3.Connection, svc: Services,
                                                         lib: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    p = propose(conn, svc, lib)["plan"]
    approve(client, "effect_plan", p["id"])
    real, n = fsops._publish, {"i": 0}

    def crash(tmp: Path, dst: Path) -> None:
        n["i"] += 1
        if n["i"] == 5:
            raise Crash()  # partial written and verified, never published
        real(tmp, dst)

    monkeypatch.setattr(fsops, "_publish", crash)
    with pytest.raises(Crash):
        eff.apply(conn, p["id"], "user:test")
    assert list(lib["library"].rglob("*.ros-partial"))
    monkeypatch.setattr(fsops, "_publish", real)
    assert eff.apply(conn, p["id"], "user:test")["state"] == "applied"
    assert not list(lib["library"].rglob("*.ros-partial"))
    assert sum(1 for v in tree(lib["library"]).values() if v != "dir") == 28
    lib_before_undo = tree(lib["library"])
    u = eff.undo(conn, p["id"], "user:test")
    assert lib_before_undo and u["state"] == "undone", u["conflicts"][:3]
    assert tree(lib["library"]) == {}


def test_locked_file_pauses_plan_then_resume(client: Any, conn: sqlite3.Connection, svc: Services, lib: dict,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    p = propose(conn, svc, lib)["plan"]
    approve(client, "effect_plan", p["id"])
    real = fsops.copy

    def locked(src: Path, dst: Path, sha: str) -> dict[str, Any]:
        if src.name == "paper05.pdf":
            e = PermissionError(13, "sharing violation")
            e.winerror = 32  # type: ignore[attr-defined]
            fsops._guard(e)
        return real(src, dst, sha)

    monkeypatch.setattr(fsops, "copy", locked)
    r = eff.apply(conn, p["id"], "user:test")
    assert r["state"] == "paused" and "sharing_violation" in r["reason"]
    done = sum(1 for e in journal.entries(conn, p["id"]) if e["phase"] == "done")
    assert done < len(p["ops"])  # nothing after the locked op ran
    monkeypatch.setattr(fsops, "copy", real)
    assert eff.apply(conn, p["id"], "user:test")["state"] == "applied"


def test_never_overwrite_and_roots(client: Any, conn: sqlite3.Connection, svc: Services, lib: dict,
                                   tmp_path: Path) -> None:
    p = propose(conn, svc, lib)["plan"]
    approve(client, "effect_plan", p["id"])
    victim = Path(next(o["dst"] for o in p["ops"] if o["op"] == "copy"))
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b"owner's own file")
    r = eff.apply(conn, p["id"], "user:test")
    assert r["state"] == "failed" and "never overwritten" in r["reason"]
    assert victim.read_bytes() == b"owner's own file"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    bad = client.post("/api/v1/library/organize", headers={"idempotency-key": uuid.uuid4().hex},
                      json={"source_dir": str(outside), "library_dir": str(lib["library"])})
    drain(conn, svc, max_tasks=60)  # also drains the parse tasks queued by the CAS import
    t = client.get(f"/api/v1/tasks/{bad.json()['id']}").json()
    assert t["state"] == "failed" and t["error_code"] == "outside_roots"
    denied = client.put("/api/v1/settings/file-roots", json={"roots": [{"path": str(svc.settings.data_dir / "db"),
                                                                        "access": "write"}]})
    assert denied.json()["code"] == "root_denied"
