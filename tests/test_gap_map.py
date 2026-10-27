"""Evidence-gap map (ADR-182): cell status from verified claims, works, weight labels and contradictions only."""

from __future__ import annotations

import sqlite3
from io import BytesIO
from typing import Any

from researchos.kernel.db import jdump, utcnow
from researchos.services.container import Services
from researchos.services.research import claims

from .m1_helpers import drain


def _pdf(*lines: str) -> bytes:
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, invariant=1)
    y = 760
    for line in lines:
        c.drawString(60, y, line)
        y -= 40
    c.showPage()
    c.save()
    return buf.getvalue()


PAPERS = {
    "a": ("EEG decoding is studied widely. We propose a CSP pipeline for motor imagery decoding.",),
    "b": ("EEG decoding is studied widely. We propose a deep network for motor imagery decoding.",),
    "c": ("EEG decoding is studied widely. We propose a common spatial pattern baseline for motor imagery.",),
}


def _claims(client: Any, conn: sqlite3.Connection, svc: Services, pid: str) -> dict[str, str]:
    vids = {k: client.post("/api/v1/documents/upload", data={"project_id": pid},
                           files={"file": (f"{k}.pdf", _pdf(*lines), "application/pdf")}).json()["version_id"]
            for k, lines in PAPERS.items()}
    drain(conn, svc)
    out = {}
    for k, vid in vids.items():
        ev = conn.execute("SELECT id, quote FROM evidence WHERE version_id=? AND kind='author_conclusion'",
                          (vid,)).fetchone()
        assert ev, k
        out[k] = claims.from_answer(conn, {"id": f"ans_{k}", "sentences": [{"text": ev[1], "evidence_ids": [ev[0]]}]},
                                    project_id=pid, origin={"origin": "test"}, actor="owner")[0]
    return out


def test_gap_map_cells(client: Any, conn: sqlite3.Connection, svc: Services) -> None:
    pid = client.post("/api/v1/projects", json={"title": "Gaps"}).json()["id"]
    ids = _claims(client, conn, svc, pid)
    axes = {"paradigm": ["motor imagery", "sleep staging"],
            "method": [["CSP", "common spatial pattern"], ["deep network", "deep learning"]]}

    def cells() -> dict[tuple[str, str], dict[str, Any]]:
        r = client.post("/api/v1/claims/gap-map", json={"project_id": pid, "axes": axes})
        assert r.status_code == 200, r.text
        return {(c["terms"]["paradigm"], c["terms"]["method"]): c for c in r.json()["cells"]}

    m = cells()
    assert m[("motor imagery", "CSP")]["status"] == "covered"  # two works; the alternative term matched paper c
    assert sorted(m[("motor imagery", "CSP")]["claims"]) == sorted([ids["a"], ids["c"]])
    assert m[("motor imagery", "deep network")]["status"] == "thin"
    assert m[("sleep staging", "CSP")]["status"] == m[("sleep staging", "deep network")]["status"] == "gap"
    conn.execute("INSERT INTO relations(src,type,dst,attrs,created_at) VALUES (?,?,?,?,?)",
                 (ids["a"], "contradicts", ids["c"], jdump({"aspect": "accuracy", "status": "suggested"}), utcnow()))
    conn.commit()
    assert cells()[("motor imagery", "CSP")]["status"] == "contested"
    conn.execute("UPDATE relations SET attrs=? WHERE type='contradicts'", (jdump({"status": "rejected"}),))
    conn.execute("UPDATE works SET kind='preprint' WHERE id IN (SELECT DISTINCT v.work_id FROM document_versions v)")
    conn.commit()
    assert cells()[("motor imagery", "CSP")]["status"] == "weak"  # rejected contradiction ignored; all preprints


def test_failed_claims_are_excluded_and_partial_words_do_not_match(client: Any, conn: sqlite3.Connection,
                                                                  svc: Services) -> None:
    pid = client.post("/api/v1/projects", json={"title": "Gaps2"}).json()["id"]
    ids = _claims(client, conn, svc, pid)
    conn.execute("UPDATE entities SET trust='failed' WHERE id=?", (ids["b"],))
    conn.commit()
    r = client.post("/api/v1/claims/gap-map", json={"project_id": pid, "axes": {"method": ["deep network", "CS"]}})
    out = r.json()
    assert out["claims_excluded_failed"] == 1
    assert [c["status"] for c in out["cells"]] == ["gap", "gap"]  # "CS" is not a whole word of "CSP"


def test_bad_axes_rejected(client: Any) -> None:
    pid = client.post("/api/v1/projects", json={"title": "Gaps3"}).json()["id"]
    for axes in ({}, {"a": ["x"], "b": ["y"], "c": ["z"]}, {"a": []}, {"a": [["", " "]]}):
        assert client.post("/api/v1/claims/gap-map", json={"project_id": pid, "axes": axes}).status_code == 422
