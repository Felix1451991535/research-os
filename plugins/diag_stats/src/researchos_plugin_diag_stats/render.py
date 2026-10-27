"""Snapshot -> Markdown body + traceability rows. Numbers come only from MetricSpan via ctx.metric, citations only
from CiteSpan via ctx.citation; nothing is computed or invented here."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from researchos.contracts.exporters import (
    Block,
    CitationEntry,
    CiteSpan,
    Diagnostic,
    ExportContext,
    MetricSpan,
    MetricValue,
    Span,
)

VERIFIED = ("recomputed", "invariant_checked")


@dataclass(frozen=True)
class MetricRow:
    ref: str
    span: MetricSpan
    value: MetricValue | None
    block: int


@dataclass(frozen=True)
class CiteRow:
    citekey: str
    entry: CitationEntry | None
    block: int


@dataclass
class Renderer:
    ctx: ExportContext
    decimals: int = 4
    diagnostics: list[Diagnostic] = field(default_factory=list)
    metrics: list[MetricRow] = field(default_factory=list)
    cites: list[CiteRow] = field(default_factory=list)
    figures: list[tuple[str, bool]] = field(default_factory=list)
    _block: int = 0

    @property
    def severity(self) -> str:
        return "error" if self.ctx.mode == "final" else "warning"

    def _diag(self, code: str, message: str, ref: str | None, severity: str | None = None) -> None:
        sev = severity or self.severity
        self.diagnostics.append(Diagnostic(sev, code, message, ref))  # type: ignore[arg-type]

    def _cite(self, s: CiteSpan) -> str:
        entry = self.ctx.citation(s.citekey)
        self.cites.append(CiteRow(s.citekey, entry, self._block))
        if entry is None:
            self._diag("unresolved_citation", f"citation {s.citekey} cannot be resolved", s.citekey)
            return f"[unresolved:{s.citekey}]"
        return f"[{s.citekey}]"

    def _metric(self, s: MetricSpan) -> str:
        m = self.ctx.metric(s.run_id, s.name, s.split)
        ref = f"M{len(self.metrics) + 1}"
        self.metrics.append(MetricRow(ref, s, m, self._block))
        where = f"{s.run_id}/{s.name}/{s.split}"
        if m is None:
            self._diag("unresolved_metric", f"metric {where} not found", s.run_id)
            return f"[missing:{where}]"
        if not math.isfinite(m.value):
            self._diag("nonfinite_metric", f"metric {where} is not a finite number", s.run_id)
            return f"[non-finite:{where}]"
        if m.verification not in VERIFIED:
            self._diag(
                "unverified_metric",
                f"metric {where} is {m.verification}, not independently verified",
                s.run_id,
                "warning",
            )
        unit = f" {m.unit}" if m.unit else ""
        return f"{m.value:.{self.decimals}f}{unit} [{ref}]"

    def span(self, s: Span) -> str:
        if isinstance(s, CiteSpan):
            return self._cite(s)
        if isinstance(s, MetricSpan):
            return self._metric(s)
        return s.text

    def spans(self, spans: tuple[Span, ...], *, cell: bool = False) -> str:
        text = "".join(self.span(s) for s in spans)
        return text.replace("|", "\\|").replace("\n", " ") if cell else text

    def _table(self, b: Block) -> list[str]:
        rows = [[self.spans(c, cell=True) for c in r] for r in b.rows]
        if not rows:
            return []
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        return [
            "| " + " | ".join(rows[0]) + " |",
            "|" + "---|" * width,
            *("| " + " | ".join(r) + " |" for r in rows[1:]),
        ]

    def _figure(self, b: Block) -> list[str]:
        aid = b.artifact_id or ""
        art = self.ctx.artifact(aid) if aid else None
        self.figures.append((aid, art is not None))
        if art is None:
            self._diag("missing_artifact", f"figure artifact {aid or '(none)'} cannot be resolved", aid or None)
        caption = self.spans(b.caption)
        return [f"*Figure ({aid or 'no artifact'}{', ' + art.mime if art else ''}):* {caption}".rstrip()]

    def block(self, index: int, b: Block) -> list[str]:
        self._block = index
        if b.kind == "heading":
            return ["#" * min(6, b.level + 1) + " " + self.spans(b.spans)]
        if b.kind == "list":
            return ["- " + self.spans(b.spans)]
        if b.kind == "table":
            return self._table(b)
        if b.kind == "figure":
            return self._figure(b)
        return [self.spans(b.spans)]

    def body(self, blocks: tuple[Block, ...]) -> list[str]:
        out: list[str] = []
        for i, b in enumerate(blocks):
            lines = self.block(i, b)
            if lines:
                out += [*lines, ""]
        return out
