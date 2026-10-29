"""Per-paper note card (research-method feature 3). A fixed `paper_card` doc per work:
problem + importance, method (design facts for the owner to judge), findings, novelty vs prior work, the authors'
own signal sentences (feature 2 Evidence), and "relation to my work" left EMPTY for the owner.
Fields are filled by the shared card mechanism (cards.py, ADR-180)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from ...sdk.texts import DEFAULT_LANGUAGE, Bound
from ..gateway.gateway import Gateway
from ..knowledge import reading
from ..research import qa  # noqa: F401  (patched by tests; cards.py calls the same module object)
from . import cards, suggestions  # noqa: F401

FIELDS: tuple[tuple[str, str], ...] = (  # (key, question); headings come from the core catalog (ADR-246)
    ("problem", "What problem does this paper address, and why is the problem important?"),
    ("method", "What data, participants, sample size, method, baseline, validation and evaluation does the study use?"),
    ("findings", "What are the main results and findings reported by the authors?"),
    ("novelty", "What is new or different compared with previous work or prior methods?"),
)


def _signals(conn: sqlite3.Connection, subject: dict[str, Any], done: dict[str, Any],
             T: Bound) -> list[tuple[str, str, str]]:
    vids = [r["version_id"] for r in conn.execute(
        "SELECT version_id FROM document_versions WHERE work_id=? AND parse_state IN ('parsed','partial')",
        (subject["work_id"],))]
    items = [m for v in vids for m in reading.listing(conn, v)["main_points"]]
    text = ("\n".join(f"- ({m['cue']}) {{{{evidence:{m['id']}}}}}" for m in items) if items
            else T("card_no_signals"))
    return [("signals", text, "author signal sentences (reading-cues)")]


SPEC = cards.CardSpec("paper_card", FIELDS, ("signals",), True, _signals)


def create(conn: sqlite3.Connection, gw: Gateway, doc_kinds: Any, *, work_id: str, project_id: str,
           actor: str, progress: dict[str, Any] | None = None,
           checkpoint: Callable[[], None] = lambda: None, language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
    return cards.create(conn, gw, doc_kinds, SPEC, {"work_id": work_id}, project_id=project_id, actor=actor,
                        progress=progress, checkpoint=checkpoint, language=language)
