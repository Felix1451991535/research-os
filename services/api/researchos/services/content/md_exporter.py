"""Core reference Exporter: Markdown (+ minimal HTML). Third-party exporters (DOCX/LaTeX) follow the same contract."""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from ...contracts.exporters import (
    Block,
    CiteSpan,
    Diagnostic,
    ExportContext,
    ExporterSpec,
    ExportFile,
    ExportResult,
    MetricSpan,
    ReportSnapshot,
    Span,
)
from ...sdk.texts import LANGUAGE_OPTION, export_language
from .texts import CORE


class MarkdownExporter:
    spec = ExporterSpec(
        "markdown",
        "1",
        ("report", "answer", "manuscript"),
        ("text/markdown", "text/html"),
        "Markdown + HTML with resolved citations and verification-labelled numbers",
        {"type": "object", "properties": dict(LANGUAGE_OPTION)},
    )

    def _span(self, s: Span, ctx: ExportContext, diags: list[Diagnostic], cited: list[str]) -> str:
        if isinstance(s, CiteSpan):
            entry = ctx.citation(s.citekey)
            if entry is None:
                diags.append(
                    Diagnostic(
                        "error" if ctx.mode == "final" else "warning",
                        "unresolved_citation",
                        f"citation {s.citekey} cannot be resolved",
                        s.citekey,
                    )
                )
                return f"[unresolved:{s.citekey}]"
            if s.citekey not in cited:
                cited.append(s.citekey)
            return f"[{s.citekey}]"
        if isinstance(s, MetricSpan):
            m = ctx.metric(s.run_id, s.name, s.split)
            if m is None:
                diags.append(
                    Diagnostic(
                        "error" if ctx.mode == "final" else "warning",
                        "unresolved_metric",
                        f"metric {s.name}/{s.split} of {s.run_id} not found",
                        s.run_id,
                    )
                )
                return "[missing]"
            return f"{m.value:.4f}{' ' + m.unit if m.unit else ''} ({m.verification})"
        return s.text

    def _block(self, b: Block, ctx: ExportContext, diags: list[Diagnostic], cited: list[str]) -> str:
        line = "".join(self._span(s, ctx, diags, cited) for s in b.spans)
        if b.kind == "heading":
            return "#" * min(6, b.level + 1) + " " + line
        if b.kind == "list":
            return "- " + line
        if b.kind == "table":
            cells = [["".join(self._span(s, ctx, diags, cited) for s in c) for c in r] for r in b.rows]
            if not cells:
                return ""
            out = ["| " + " | ".join(cells[0]) + " |", "|" + "---|" * len(cells[0])]
            return "\n".join(out + ["| " + " | ".join(r) + " |" for r in cells[1:]])
        if b.kind == "figure":
            a = ctx.artifact(b.artifact_id or "")
            if a is None:
                diags.append(
                    Diagnostic("error", "missing_artifact", f"artifact {b.artifact_id} not found", b.artifact_id)
                )
                return "[missing figure]"
            cap = "".join(self._span(s, ctx, diags, cited) for s in b.caption)
            return f"![{cap}]({a.path.name}) (sha256 {a.sha256[:12]})"
        return line

    def export(self, snapshot: ReportSnapshot, ctx: ExportContext, options: Mapping[str, Any]) -> ExportResult:
        T = CORE.lang(export_language(options))
        diags: list[Diagnostic] = []
        cited: list[str] = []
        body = [f"# {snapshot.title}", ""] + [self._block(b, ctx, diags, cited) + "\n" for b in snapshot.blocks]
        if cited:
            body.append(f"## {T('references')}")
            for k in cited:
                e = ctx.citation(k)
                if e is not None:
                    r = e.record
                    ids = ", ".join(f"{s}:{v}" for s, v in r.identifiers)
                    body.append(
                        f"- [{k}] {', '.join(r.authors) or T('unknown_authors')}. {r.title}."
                        f"{' ' + r.venue + '.' if r.venue else ''}{' ' + str(r.year) + '.' if r.year else ''}"
                        f"{' ' + ids if ids else ''} ({T('evidence_passages', n=len(e.evidence))})"
                    )
        md = "\n".join(body) + "\n"
        (ctx.out_dir / "report.md").write_text(md, encoding="utf-8")
        (ctx.out_dir / "report.html").write_text(
            f"<!doctype html><html lang='{T.language}'><meta charset='utf-8'>"
            f"<title>{html.escape(snapshot.title)}</title>"
            f"<pre style='font:14px/1.5 system-ui;white-space:pre-wrap'>{html.escape(md)}</pre>",
            encoding="utf-8",
        )
        return ExportResult(
            (ExportFile("report.md", "text/markdown"), ExportFile("report.html", "text/html")), tuple(diags)
        )
