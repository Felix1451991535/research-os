"""Citation + number gates for authored docs (K8 invariant, ARCHITECTURE §5.1 "numbers in prose"):
- every `{{cite:key}}` resolves to a library work (retracted works are flagged);
- every `{{metric:run/name/split}}` resolves to a stored metric (failed verification = error, self-reported =
  warning);
- every `{{evidence:id}}` re-verifies against its source page now (stale/missing/failed trust = error);
- no FREE numbers: a number in prose is allowed only inside a placeholder, inside `{{lit:...}}`, in an exempt
  pattern (figure/table/section/equation refs, heading numbers, years, names like GPT-4 / COVID-19), or when it
  occurs in the quote of evidence cited in the same block (number provenance, quote-or-reject).
Severity: `final` -> error (export refused); `draft` -> warning. Malformed placeholders are always errors."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from ...contracts.exporters import CiteSpan, Diagnostic, MetricSpan, TextSpan
from ...kernel.db import jload, row
from ..content.citekey_index import lookup_work
from ..knowledge import evidence as ev
from ..knowledge.text import EXEMPT, PROSE_NUMBER_RE, canon_number
from ..literature import citekeys
from .doc_snapshot import EvidenceSpan, SourceBlock, SourceSpan

HEADING_NUM = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s")
Mode = Literal["draft", "final"]


@dataclass
class GateResult:
    diagnostics: list[Diagnostic] = field(default_factory=list)
    evidence_cite: dict[str, tuple[str, int]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: {"citations": 0, "metrics": 0, "evidence": 0,
                                                            "free_numbers": 0})

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]


class _Gate:
    def __init__(self, conn: sqlite3.Connection, mode: Mode) -> None:
        self.conn, self.mode, self.res = conn, mode, GateResult()
        self.sev: Literal["warning", "error"] = "error" if mode == "final" else "warning"

    def add(self, code: str, msg: str, ref: str | None, always_error: bool = False) -> None:
        self.res.diagnostics.append(Diagnostic("error" if always_error else self.sev, code, msg, ref))

    def cite(self, s: CiteSpan) -> None:
        self.res.counts["citations"] += 1
        c = lookup_work(self.conn, s.citekey)
        if c is None:
            self.add("unresolved_citation", f"citekey {s.citekey!r} is not in the library", s.citekey)
        elif (jload(c["retraction"]) or {}).get("retracted"):
            self.add("retracted_citation", f"{s.citekey} cites a RETRACTED work", s.citekey)

    def metric(self, s: MetricSpan) -> None:
        self.res.counts["metrics"] += 1
        ref = f"{s.run_id}/{s.name}/{s.split}"
        m = row(self.conn.execute("SELECT value, verification FROM run_metrics WHERE run_id=? AND name=? AND split=?",
                                  (s.run_id, s.name, s.split)))
        if m is None or m["value"] is None:
            self.add("unresolved_metric", f"no stored metric {ref}", ref)
        elif m["verification"] == "failed":
            self.add("metric_failed", f"{ref} failed recomputation", ref)
        elif m["verification"] in ("self_reported", "unverified"):
            self.res.diagnostics.append(Diagnostic("warning", "metric_unverified", f"{ref} is {m['verification']}",
                                                   ref))

    def evidence(self, s: EvidenceSpan) -> str:
        self.res.counts["evidence"] += 1
        v = ev.verify(self.conn, s.evidence_id)
        if v.status in ("missing", "stale") or v.evidence is None:
            self.add(f"evidence_{v.status}", f"evidence {s.evidence_id}: {v.detail}", s.evidence_id)
            return ""
        e = v.evidence
        if e["trust"] == "failed":
            self.add("evidence_failed", f"evidence {s.evidence_id} has trust 'failed' (e.g. retracted source)",
                     s.evidence_id)
        if v.status == "superseded":
            self.res.diagnostics.append(Diagnostic("warning", "evidence_superseded",
                                                   f"evidence {s.evidence_id} is on an older version", s.evidence_id))
        w = row(self.conn.execute("SELECT work_id FROM document_versions WHERE version_id=?", (e["version_id"],)))
        if w:
            self.res.evidence_cite[s.evidence_id] = (citekeys.assign(self.conn, w["work_id"]), int(e["page_index"]))
        return str(e["quote"])

    def unit(self, ss: tuple[SourceSpan, ...], heading: bool = False) -> None:
        """One block/cell: resolve placeholders first, then check numbers against cited quotes."""
        quotes = []
        for s in ss:
            if isinstance(s, CiteSpan):
                self.cite(s)
            elif isinstance(s, MetricSpan):
                self.metric(s)
            elif isinstance(s, EvidenceSpan):
                quotes.append(self.evidence(s))
        sourced = {canon_number(m.group(0)) for q in quotes for m in PROSE_NUMBER_RE.finditer(q)}
        for s in ss:
            if isinstance(s, TextSpan):
                self.numbers(s.text, sourced, heading)

    def numbers(self, text: str, sourced: set[str], heading: bool) -> None:
        if "{{" in text:
            self.add("malformed_placeholder", f"unrecognised placeholder in: {text[:80]!r}", None, always_error=True)
        t = HEADING_NUM.sub(" ", text) if heading else text
        t = EXEMPT.sub(" ", t)
        for m in PROSE_NUMBER_RE.finditer(t):
            if canon_number(m.group(0)) not in sourced:
                self.res.counts["free_numbers"] += 1
                self.add("free_number", f"number {m.group(0)!r} has no source: use {{{{metric:...}}}}, cite "
                         f"evidence whose quote contains it, or {{{{lit:...}}}} if it is not data", m.group(0))


def check(conn: sqlite3.Connection, blocks: list[SourceBlock], mode: Mode) -> GateResult:
    g = _Gate(conn, mode)
    for b in blocks:
        g.unit(b.spans, heading=b.kind == "heading")
        for r in b.rows:
            for cell in r:
                g.unit(cell)
        if b.caption:
            g.unit(b.caption)
        if b.kind == "figure" and b.artifact_id and row(
                conn.execute("SELECT 1 FROM artifacts WHERE id=?", (b.artifact_id,))) is None:
            g.add("missing_artifact", f"figure artifact {b.artifact_id} not found", b.artifact_id)
    return g.res
