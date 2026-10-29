"""Shared fixture data for the title identification tests (test_identify_title*.py): two synthetic PDFs, one whose
page-1 title matches a fake source record, and the library folders."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from researchos.services.container import Services
from researchos.services.knowledge.writer import DbKnowledgeWriter
from researchos.services.literature import organize
from researchos.services.literature.discovery import Ctx

from .m5_helpers import FakeSource, rec, register
from .m5_library_fixture import _pdf

TITLE_A = "Heart rate variability during sleep in healthy adults"
TITLE_B = "A local workshop summary on lab safety routines"


def make_lib(tmp_path: Path, client: Any, svc: Services) -> dict[str, Any]:
    src, library = tmp_path / "Downloads", tmp_path / "Library"
    src.mkdir()
    library.mkdir()
    _pdf(src / "scan_0042.pdf", ["Journal of Synthetic Signals, vol. 3", TITLE_A, "Body text, no identifiers."])
    _pdf(src / "workshop.pdf", [TITLE_B, "Notes."])
    source = FakeSource("fake_id", hits={
        TITLE_A: [rec("fake_id", "a", "Heart Rate Variability During Sleep in Healthy Adults.", "10.5555/id.a"),
                  rec("fake_id", "m", "Gut microbiome composition in mice", "10.5555/id.m")],
        TITLE_B: [rec("fake_id", "z", "Workshop summaries considered harmful", "10.5555/id.z")]})
    register(svc, source)
    r = client.put("/api/v1/settings/file-roots", json={"roots": [{"path": str(src), "access": "read"},
                                                                  {"path": str(library), "access": "write"}]})
    assert r.status_code == 200, r.text
    return {"src": src, "library": library, "source": source}


def propose(conn: sqlite3.Connection, svc: Services, lib: dict[str, Any]) -> dict[str, Any]:
    writer = DbKnowledgeWriter(conn, svc.blobs, svc.kinds, plugin="core.library", actor="user:test",
                               project_id=None, max_bytes=50_000_000)
    return organize.propose(conn, Ctx(svc.extensions, svc.keystore, "user:test"), writer,
                            svc.settings.data_dir / "home", source_dir=str(lib["src"]),
                            library_dir=str(lib["library"]))


def second_suggestion(conn: sqlite3.Connection, sug: dict[str, Any]) -> str:
    """A second open suggestion for the same document (the other candidate the title search returned)."""
    other = conn.execute("SELECT id FROM lit_candidates WHERE id<>? ORDER BY id LIMIT 1",
                         (sug["candidate_id"],)).fetchone()[0]
    conn.execute("INSERT INTO doc_identifications(id, version_id, candidate_id, title_guess, score, created_at) "
                 "VALUES ('idn_second', ?, ?, ?, 0.9, ?)", (sug["version_id"], other, sug["title_guess"],
                                                            sug["created_at"]))
    conn.commit()
    return "idn_second"
