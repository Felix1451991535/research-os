"""Deterministic validation of a model's cited answer (no model in the loop):
- JSON schema; every cited id must be one of the passages actually provided (forged ids dropped);
- numbers in a sentence must occur in its cited passages (number provenance);
- kind must be one of the four evidence kinds."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..gateway.router import Signals
from ..knowledge.text import prose_numbers_missing

KINDS = {"source_fact", "author_conclusion", "model_inference", "hypothesis"}


@dataclass
class Checked:
    kept: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    self_reported: float | None = None


def check_answer(text: str, passages: dict[str, str]) -> tuple[Signals, Checked]:
    try:
        obj = json.loads(text)
        sentences = obj["sentences"]
        if not isinstance(sentences, list):
            raise TypeError("sentences must be a list")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise ValueError(f"answer is not valid JSON of the required shape: {e}") from e
    out = Checked(self_reported=_conf(obj.get("confidence")))
    for s in sentences:
        reason = _problem(s, passages)
        (out.dropped if reason else out.kept).append({**s, "reason": reason} if reason else s)
    total = len(out.kept) + len(out.dropped)
    coverage = 1.0 if total == 0 else len(out.kept) / total
    return Signals(validators_passed=1.0, evidence_coverage=coverage, self_reported=out.self_reported), out


def _conf(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _problem(s: Any, passages: dict[str, str]) -> str | None:
    if not isinstance(s, dict) or not isinstance(s.get("text"), str) or not s["text"].strip():
        return "malformed sentence"
    ids = s.get("evidence") or []
    if s.get("kind", "source_fact") not in KINDS:
        return "unknown kind"
    if s.get("kind", "source_fact") in ("source_fact", "author_conclusion") and not ids:
        return "factual sentence without evidence"
    forged = [i for i in ids if i not in passages]
    if forged:
        return f"cites passages that were not provided: {forged}"
    joined = " ".join(passages[i] for i in ids)
    if ids and prose_numbers_missing(s["text"], joined):
        return f"numbers not found in cited passages: {prose_numbers_missing(s['text'], joined)}"
    return None
