"""Exporter stats_report: statistics appendix (Markdown + CSV) with a reporting checklist and a number-traceability
table. Final mode with any unresolved citation, metric or figure -> error diagnostics and no files written."""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

from researchos.contracts.exporters import (
    Diagnostic,
    ExportContext,
    ExporterSpec,
    ExportFile,
    ExportResult,
    ReportSnapshot,
)

from .render import VERIFIED, Renderer

_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
OPTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "basename": {"type": "string", "pattern": _BASENAME.pattern, "default": "stats_appendix"},
        "decimals": {
            "type": "integer",
            "minimum": 4,
            "maximum": 10,
            "default": 4,
            "description": "decimals in Markdown (the CSV keeps full precision)",
        },
    },
}
CSV_COLUMNS = (
    "ref",
    "kind",
    "status",
    "run_id",
    "metric",
    "split",
    "value",
    "unit",
    "verification",
    "citekey",
    "title",
    "year",
    "block",
)
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _check(ok: bool) -> str:
    return "[x]" if ok else "[ ]"


def _csv_text(value: str) -> str:
    """Neutralise spreadsheet formula injection in free-text cells (numbers are written separately)."""
    return "'" + value if value.startswith(_FORMULA_START) else value


class StatsReportExporter:
    spec = ExporterSpec(
        name="stats_report",
        version="0.1.0",
        doc_kinds=("report", "manuscript", "answer"),
        mime_types=("text/markdown", "text/csv"),
        description="Statistics appendix (Markdown + CSV): reporting checklist, number traceability table with "
        "verification status, references; numbers only from MetricSpan, citations only from CiteSpan",
        options_schema=OPTIONS_SCHEMA,
    )

    def export(self, snapshot: ReportSnapshot, ctx: ExportContext, options: Mapping[str, Any]) -> ExportResult:
        opts = _options(options)
        if isinstance(opts, Diagnostic):
            return ExportResult((), (opts,))
        basename, decimals = opts
        if snapshot.kind not in self.spec.doc_kinds:
            return ExportResult(
                (), (Diagnostic("error", "unsupported_kind", f"document kind {snapshot.kind!r} is not supported"),)
            )
        r = Renderer(ctx, decimals)
        body = r.body(snapshot.blocks)
        diags = tuple(r.diagnostics)
        if ctx.mode == "final" and any(d.severity == "error" for d in diags):
            return ExportResult((), diags)
        md = "\n".join([*_front(snapshot, ctx.mode), *body, *_checklist(r), *_trace(r), *_references(r)])
        out = ctx.out_dir
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{basename}.md").write_text(md.rstrip() + "\n", encoding="utf-8", newline="\n")
        (out / f"{basename}.csv").write_text(_csv(r), encoding="utf-8", newline="")
        files = (ExportFile(f"{basename}.md", "text/markdown"), ExportFile(f"{basename}.csv", "text/csv"))
        return ExportResult(files, diags)


def _options(options: Mapping[str, Any]) -> tuple[str, int] | Diagnostic:
    unknown = set(options) - set(OPTIONS_SCHEMA["properties"])
    basename, decimals = options.get("basename", "stats_appendix"), options.get("decimals", 4)
    if unknown:
        return Diagnostic("error", "invalid_options", f"unknown options: {', '.join(sorted(unknown))}")
    if not isinstance(basename, str) or not _BASENAME.match(basename):
        return Diagnostic("error", "invalid_options", "basename must match " + _BASENAME.pattern)
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 4 <= decimals <= 10:
        return Diagnostic("error", "invalid_options", "decimals must be an integer in 4..10")
    return basename, decimals


def _front(s: ReportSnapshot, mode: str) -> list[str]:
    return [
        f"# Statistics appendix: {s.title}",
        "",
        f"Document `{s.doc_id}` revision {s.revision} ({s.kind}, language {s.language}); export mode: {mode}.",
        "Numbers marked [Mn] are resolved from stored run metrics; see the traceability table.",
        "",
    ]


def _checklist(r: Renderer) -> list[str]:
    resolved = [m for m in r.metrics if m.value is not None]
    status = Counter(m.value.verification for m in resolved if m.value is not None)
    verified = sum(status[v] for v in VERIFIED)
    cites_ok = [c for c in r.cites if c.entry is not None]
    figs_ok = [f for f in r.figures if f[1]]
    splits = sorted({m.span.split for m in r.metrics})
    breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(status.items())) or "none"
    return [
        "## Statistical reporting checklist",
        "",
        f"- {_check(len(resolved) == len(r.metrics))} Every reported number resolves to a stored run metric "
        f"({len(resolved)} of {len(r.metrics)}).",
        f"- {_check(verified == len(resolved))} Numbers are recomputed or invariant-checked "
        f"({verified} of {len(resolved)}; {breakdown}).",
        f"- {_check(bool(splits))} Evaluation split stated for every number (splits: {', '.join(splits) or 'none'}).",
        f"- {_check(len(cites_ok) == len(r.cites))} Every citation resolves to a bibliographic record "
        f"({len(cites_ok)} of {len(r.cites)}).",
        f"- {_check(len(figs_ok) == len(r.figures))} Every figure resolves to a stored artifact "
        f"({len(figs_ok)} of {len(r.figures)}).",
        "- Study-design items (participants, reference standard, sample size, handling of missing data; e.g. "
        "STARD 2015 / TRIPOD) are not assessed automatically and need a human check.",
        "",
    ]


def _trace(r: Renderer) -> list[str]:
    if not r.metrics:
        return ["## Number traceability", "", "No numbers in this document.", ""]
    rows = [
        "## Number traceability",
        "",
        "| Ref | Value | Unit | Run | Metric | Split | Verification | Block |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in r.metrics:
        v = m.value
        value = "unresolved" if v is None else repr(v.value)
        cells = (
            m.ref,
            value,
            (v.unit or "") if v else "",
            m.span.run_id,
            m.span.name,
            m.span.split,
            v.verification if v else "unresolved",
            str(m.block),
        )
        rows.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return [*rows, ""]


def _references(r: Renderer) -> list[str]:
    seen: dict[str, str] = {}
    for c in r.cites:
        if c.entry is None or c.citekey in seen:
            continue
        rec = c.entry.record
        ids = ", ".join(f"{scheme}:{value}" for scheme, value in rec.identifiers)
        parts = [", ".join(rec.authors) or None, f"({rec.year})" if rec.year else None, rec.title, rec.venue, ids]
        seen[c.citekey] = f"- [{c.citekey}] " + ". ".join(p for p in parts if p)
    return ["## References", "", *(seen.values() or ["No citations."]), ""]


def _csv(r: Renderer) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for m in r.metrics:
        v = m.value
        w.writerow(
            [
                m.ref,
                "metric",
                "resolved" if v else "unresolved",
                _csv_text(m.span.run_id),
                _csv_text(m.span.name),
                _csv_text(m.span.split),
                repr(v.value) if v else "",
                _csv_text(v.unit or "") if v else "",
                _csv_text(v.verification) if v else "",
                "",
                "",
                "",
                m.block,
            ]
        )
    for i, c in enumerate(r.cites, 1):
        rec = c.entry.record if c.entry else None
        w.writerow(
            [
                f"C{i}",
                "citation",
                "resolved" if rec else "unresolved",
                "",
                "",
                "",
                "",
                "",
                "",
                _csv_text(c.citekey),
                _csv_text(rec.title) if rec else "",
                rec.year if rec and rec.year else "",
                c.block,
            ]
        )
    return buf.getvalue()
