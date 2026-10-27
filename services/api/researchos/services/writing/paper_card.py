"""Per-paper note card (research-method feature 3). A fixed `paper_card` doc per work:
problem + importance, method (design facts for the owner to judge), findings, novelty vs prior work, the authors'
own signal sentences (feature 2 Evidence), and "relation to my work" left EMPTY for the owner.
Fields are filled by the shared card mechanism (cards.py, ADR-180)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from ..gateway.gateway import Gateway
from ..knowledge import reading
from ..research import qa  # noqa: F401  (patched by tests; cards.py calls the same module object)
from . import cards, suggestions  # noqa: F401

FIELDS: tuple[tuple[str, str, str], ...] = (
    ("problem", "Problem and importance", "What problem does this paper address, and why is the problem important?"),
    ("method", "Method (design facts; quality is the owner's judgement)",
     "What data, participants, sample size, method, baseline, validation and evaluation does the study use?"),
    ("findings", "Findings", "What are the main results and findings reported by the authors?"),
    ("novelty", "Novelty vs prior work", "What is new or different compared with previous work or prior methods?"),
)
OWNER_FIELD = "Relation to my work"


def _signals(conn: sqlite3.Connection, subject: dict[str, Any], done: dict[str, Any]) -> list[tuple[str, str, str]]:
    vids = [r["version_id"] for r in conn.execute(
        "SELECT version_id FROM document_versions WHERE work_id=? AND parse_state IN ('parsed','partial')",
        (subject["work_id"],))]
    items = [m for v in vids for m in reading.listing(conn, v)["main_points"]]
    text = ("\n".join(f"- ({m['cue']}) {{{{evidence:{m['id']}}}}}" for m in items) if items
            else "Insufficient material: no author signal sentences located in the full text.")
    return [("signals", text, "author signal sentences (reading-cues)")]


SPEC = cards.CardSpec("paper_card", "Card", FIELDS, (("signals", "Author signals (verbatim, located)"),),
                      OWNER_FIELD, _signals)


def create(conn: sqlite3.Connection, gw: Gateway, doc_kinds: Any, *, work_id: str, project_id: str,
           actor: str, progress: dict[str, Any] | None = None,
           checkpoint: Callable[[], None] = lambda: None) -> dict[str, Any]:
    return cards.create(conn, gw, doc_kinds, SPEC, {"work_id": work_id}, project_id=project_id, actor=actor,
                        progress=progress, checkpoint=checkpoint)
