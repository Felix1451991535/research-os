"""Accuracy evaluation + certification (ARCHITECTURE §5.4, ADR-011).

G3 items (JSONL, owner-verified) reference library documents by sha256. An eval run answers every item through the
normal QA pipeline in `eval_mode` (certification gate bypassed, results labelled), computes:
  locate_rate       = cited evidence that re-verifies / all cited evidence
  support_precision = kept sentences citing a gold page / kept sentences (answerable items)
  refusal_rate      = unanswerable items answered "insufficient_material" / unanswerable items
and records an eval_runs row for (model, prompt hash). Items with verified=false are never used to certify.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...kernel.db import row
from ..gateway import certification, ledger
from ..gateway.gateway import Gateway
from ..knowledge import evidence as ev
from .qa import QAContext, answer
from .qa_prompts import CITED_QA


@dataclass
class EvalReport:
    items: int = 0
    skipped_unverified: int = 0
    metrics: dict[str, float] = field(default_factory=dict)
    per_item: list[dict[str, Any]] = field(default_factory=list)
    stopped: str | None = None
    eval_id: str | None = None


def load_items(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _gold_pages(conn: sqlite3.Connection, item: dict[str, Any]) -> set[tuple[str, int]]:
    out = set()
    for g in item.get("gold", []):
        v = row(conn.execute("SELECT version_id FROM document_versions WHERE sha256=?", (g["sha256"],)))
        if v:
            out.add((v["version_id"], int(g["page"])))
    return out


def _score_item(conn: sqlite3.Connection, item: dict[str, Any], res: dict[str, Any], acc: dict[str, int]) -> None:
    gold = _gold_pages(conn, item)
    for s in res.get("sentences", []):
        ids = s.get("evidence_ids", [])
        acc["cited"] += len(ids)
        acc["located"] += sum(1 for e in ids if ev.verify(conn, e).status in ("ok", "superseded"))
        if item.get("answerable", True):
            acc["kept"] += 1
            pages = (
                {
                    (r[0], r[1])
                    for r in conn.execute(
                        f"SELECT version_id, page_index FROM evidence WHERE id IN ({','.join('?' * len(ids))})", ids
                    )
                }
                if ids
                else set()
            )
            acc["supported"] += int(bool(pages & gold))
    if not item.get("answerable", True):
        acc["unanswerable"] += 1
        acc["refused"] += int(res["status"] == "insufficient_material")


def run_g3(
    conn: sqlite3.Connection, gw: Gateway, items: list[dict[str, Any]], *, budget_cny: float, actor: str = "system:eval"
) -> EvalReport:
    rep = EvalReport()
    acc = dict.fromkeys(("cited", "located", "kept", "supported", "unanswerable", "refused"), 0)
    start = ledger.month_summary(conn)
    model = None
    for item in items:
        if not item.get("verified"):
            rep.skipped_unverified += 1
            continue
        now = ledger.month_summary(conn)
        if (now["settled"] + now["reserved"]) - (start["settled"] + start["reserved"]) >= budget_cny:
            rep.stopped = "eval budget reached"
            break
        res = answer(conn, gw, QAContext(item["question"], None, actor, f"eval:{item['id']}", eval_mode=True))
        model = res.get("model") or model
        _score_item(conn, item, res, acc)
        rep.items += 1
        rep.per_item.append({"id": item["id"], "status": res["status"], "sentences": len(res.get("sentences", []))})
    rep.metrics = {
        "locate_rate": acc["located"] / acc["cited"] if acc["cited"] else 1.0,
        "support_precision": acc["supported"] / acc["kept"] if acc["kept"] else 0.0,
        "refusal_rate": acc["refused"] / acc["unanswerable"] if acc["unanswerable"] else 0.0,
        "n_items": float(rep.items),
    }
    if model and rep.items and not rep.stopped:
        rep.eval_id = certification.record(conn, "g3", model, CITED_QA.hash, rep.metrics)
    return rep
