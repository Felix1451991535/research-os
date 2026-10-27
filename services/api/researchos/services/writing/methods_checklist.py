"""Checkable methods checklist (V1 item 3, ADR-180): a fixed `methods_checklist` doc per work whose items are
cited QA answers on that work's full text (each statement carries evidence with its page), plus a deterministic
"Missing conditions" section listing every item without a verified, cited statement - what a reproduction cannot
take from the paper as retrieved and must check, ask for or decide."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from ..gateway.gateway import Gateway
from . import cards

ITEMS: tuple[tuple[str, str, str], ...] = (
    ("data", "Data source and licence", "Which dataset or data source is used, which version, and under what licence "
     "or access terms?"),
    ("participants", "Participants", "How many participants or subjects are included, and what are the inclusion "
     "and exclusion criteria?"),
    ("acquisition", "Acquisition", "How were the signals or data acquired: device, sampling rate, channels or "
     "electrodes, and recording protocol?"),
    ("preprocessing", "Preprocessing", "What preprocessing is applied: filters with cut-off frequencies, "
     "re-referencing, artifact handling, normalisation?"),
    ("epoching", "Epochs and labels", "How are trials, windows or epochs defined (onsets, lengths) and how are "
     "labels assigned?"),
    ("split", "Split protocol", "How are data split into training, validation and test sets; is the split by "
     "subject or session, and is cross-validation used?"),
    ("model", "Features and model", "Which features and which model or classifier are used?"),
    ("hyperparameters", "Hyperparameters", "Which hyperparameter values are used and how were they chosen?"),
    ("training", "Training", "How is the model trained: optimiser, epochs, random seeds, repetitions, hardware?"),
    ("metrics", "Metrics", "Which evaluation metrics are reported, and on which data?"),
    ("statistics", "Statistics", "Which statistical tests, confidence intervals or chance levels are reported?"),
    ("results", "Main numbers", "What are the main quantitative results (values with their metric and data)?"),
    ("availability", "Code and data availability", "Are the code and data available, and where?"),
)
MISSING = "missing"


def established(field: dict[str, Any]) -> bool:
    """An item is established only by a verified, cited statement of kind source_fact - a fact from the paper.
    Model inferences and statements about the retrieved passages ("the passages do not state ...") are shown in
    the item but never establish it (ADR-199, found in the V1 acceptance)."""
    return bool(field.get("cited")) and field.get("kinds", {}).get("source_fact", 0) > 0


def missing(conn: sqlite3.Connection, subject: dict[str, Any], done: dict[str, Any]) -> list[tuple[str, str, str]]:
    """An item that is not established is "not established": retrieval and verification found no supporting fact,
    which is not proof that the paper omits it. An item whose QA could not run at all (e.g. no model configured) is
    listed separately as unresolved (review #67)."""
    ok = ("answered", "insufficient_material", None)
    absent = [h for k, h, _ in ITEMS if not established(done.get(k, {})) and done.get(k, {}).get("status") in ok]
    unresolved = [(h, done[k]["status"]) for k, h, _ in ITEMS if not established(done.get(k, {}))
                  and done.get(k, {}).get("status") not in ok]
    lines = [f"- {h}: not established by retrieved, verified evidence" for h in absent]
    lines += [f"- {h}: unresolved, the question could not be checked ({st})" for h, st in unresolved]
    text = "\n".join(lines) or "None: every item has a verified, cited statement."
    return [(MISSING, text, f"{len(absent)} not established, {len(unresolved)} unresolved of {len(ITEMS)} items")]


SPEC = cards.CardSpec("methods_checklist", "Methods checklist", ITEMS, ((MISSING, "Missing conditions"),),
                      "Reproduction notes", missing)


def create(conn: sqlite3.Connection, gw: Gateway, doc_kinds: Any, *, work_id: str, project_id: str,
           actor: str, progress: dict[str, Any] | None = None,
           checkpoint: Callable[[], None] = lambda: None) -> dict[str, Any]:
    return cards.create(conn, gw, doc_kinds, SPEC, {"work_id": work_id}, project_id=project_id, actor=actor,
                        progress=progress, checkpoint=checkpoint)
