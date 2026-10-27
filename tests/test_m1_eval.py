"""G3 evaluation runner: metrics, unverified items never certify, budget cap, certification recorded."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from researchos.services.container import Services
from researchos.services.gateway import certification
from researchos.services.research.evaluation import load_items, run_g3
from researchos.services.research.qa_prompts import CITED_QA

from .conftest import Client
from .gold.g1_generator import single_column
from .m1_helpers import configure_models, drain


def test_g3_run_certifies(client: Client, conn: sqlite3.Connection, svc: Services, mocks: dict, tmp_path: Path) -> None:
    data = single_column().data
    client.post("/api/v1/documents/upload", files={"file": ("har.pdf", data, "application/pdf")})
    drain(conn, svc)
    configure_models(conn, svc, ("deepseek",), certify=False)
    sha = hashlib.sha256(data).hexdigest()

    def respond(req):  # cites the passage containing "0.91" if present, else nothing
        user = req.messages[1].content
        label = next(
            (
                ln[1 : ln.index("]")]
                for ln in user.splitlines()
                if ln.startswith("[E") and "0.91" in user.split(ln, 1)[1][:900]
            ),
            None,
        )
        s = (
            [{"text": "The random forest reached 0.91 macro F1.", "evidence": [label], "kind": "source_fact"}]
            if label
            else []
        )
        return json.dumps({"sentences": s, "confidence": 0.9})

    mocks["deepseek"].responder = respond
    items = [
        {
            "id": "q1",
            "question": "random forest macro F1 baseline",
            "answerable": True,
            "verified": True,
            "gold": [{"sha256": sha, "page": 1}],
        },
        {"id": "q2", "question": "quantum chromodynamics lattice gauge", "answerable": False, "verified": True},
        {"id": "q3", "question": "draft item", "answerable": True, "verified": False},
    ]
    p = tmp_path / "g3.jsonl"
    p.write_text("\n".join(json.dumps(i) for i in items))
    rep = run_g3(conn, svc.gateway, load_items(p), budget_cny=5.0)
    assert rep.items == 2 and rep.skipped_unverified == 1
    assert rep.metrics["locate_rate"] == 1.0 and rep.metrics["support_precision"] == 1.0
    assert rep.metrics["refusal_rate"] == 1.0
    assert certification.status(conn, "deepseek-v4", CITED_QA.hash) == "certified"


def test_g3_budget_cap_stops_without_certifying(
    client: Client, conn: sqlite3.Connection, svc: Services, tmp_path: Path
) -> None:
    client.post("/api/v1/documents/upload", files={"file": ("har.pdf", single_column().data, "application/pdf")})
    drain(conn, svc)
    configure_models(conn, svc, ("deepseek",), certify=False)
    items = [
        {"id": f"q{i}", "question": "random forest macro F1", "verified": True, "answerable": True} for i in range(3)
    ]
    rep = run_g3(conn, svc.gateway, items, budget_cny=0.0)
    assert rep.stopped and rep.eval_id is None
    assert certification.status(conn, "deepseek-v4", CITED_QA.hash) == "unevaluated"
