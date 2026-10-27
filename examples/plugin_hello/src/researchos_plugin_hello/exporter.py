"""Plain-text exporter: resolves cite/metric spans through the ExportContext; final mode refuses unresolved ones."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from researchos.contracts.exporters import (
    CiteSpan,
    Diagnostic,
    ExportContext,
    ExporterSpec,
    ExportFile,
    ExportResult,
    MetricSpan,
    ReportSnapshot,
)


class PlainTextExporter:
    spec = ExporterSpec("hello_plaintext", "0.1.0", ("report", "answer"), ("text/plain",), "plain text + references")

    def export(self, snapshot: ReportSnapshot, ctx: ExportContext, options: Mapping[str, Any]) -> ExportResult:
        diags: list[Diagnostic] = []
        lines, refs = [snapshot.title.upper(), ""], []
        for b in snapshot.blocks:
            parts = []
            for s in b.spans:
                if isinstance(s, CiteSpan):
                    e = ctx.citation(s.citekey)
                    if e is None:
                        diags.append(
                            Diagnostic(
                                "error" if ctx.mode == "final" else "warning",
                                "unresolved_citation",
                                f"cannot resolve {s.citekey}",
                                s.citekey,
                            )
                        )
                        parts.append("[?]")
                    else:
                        refs.append(f"[{s.citekey}] {e.record.title} ({e.record.year or 'n.d.'})")
                        parts.append(f"[{s.citekey}]")
                elif isinstance(s, MetricSpan):
                    m = ctx.metric(s.run_id, s.name, s.split)
                    parts.append(f"{m.value:.4f} ({m.verification})" if m else "[missing]")
                    if m is None:
                        diags.append(Diagnostic("error", "unresolved_metric", s.name, s.run_id))
                else:
                    parts.append(s.text)
            lines.append("".join(parts))
        (ctx.out_dir / "report.txt").write_text("\n".join(lines + ["", "REFERENCES", *refs]) + "\n", encoding="utf-8")
        return ExportResult((ExportFile("report.txt", "text/plain"),), tuple(diags))
