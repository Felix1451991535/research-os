"""Conformance for Exporter: files confined to out_dir and declared MIME types, citations/numbers preserved,
final mode refuses unresolved citations or metrics."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...contracts.exporters import Block, CiteSpan, Exporter, ExportResult, MetricSpan, ReportSnapshot, TextSpan
from ..testing import FakeExportContext


class ExporterConformance:
    def make_exporter(self) -> Exporter:
        raise NotImplementedError

    def sample_snapshot(self) -> ReportSnapshot:
        raise NotImplementedError

    def make_context(self, out_dir: Path) -> FakeExportContext:
        """Must resolve every cite/metric span of sample_snapshot()."""
        raise NotImplementedError

    def options(self) -> dict[str, Any]:
        return {}

    def rendered_text(self, out_dir: Path, result: ExportResult) -> str:
        """Plain text of the outputs for semantic checks (override for binary formats such as DOCX)."""
        return "\n".join((out_dir / f.rel_path).read_text(encoding="utf-8", errors="ignore") for f in result.files)

    def _export(self, snapshot: ReportSnapshot, out: Path, mode: str = "final") -> ExportResult:
        ctx = self.make_context(out)
        ctx.mode = mode  # type: ignore[assignment]
        return self.make_exporter().export(snapshot, ctx, self.options())

    def test_outputs_confined_and_declared(self) -> None:
        exp = self.make_exporter()
        with tempfile.TemporaryDirectory() as td:
            res = self._export(self.sample_snapshot(), Path(td))
            assert res.files and not [d for d in res.diagnostics if d.severity == "error"]
            for f in res.files:
                p = (Path(td) / f.rel_path).resolve()
                assert p.is_relative_to(Path(td).resolve()) and p.is_file(), f.rel_path
                assert f.mime in exp.spec.mime_types, f.mime

    def test_citations_and_numbers_preserved(self) -> None:
        snap = self.sample_snapshot()
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            ctx = self.make_context(out)
            res = self.make_exporter().export(snap, ctx, self.options())
            text = self.rendered_text(out, res)
            spans = [s for b in snap.blocks for s in b.spans]
            for s in spans:
                if isinstance(s, CiteSpan):
                    entry = ctx.citation(s.citekey)
                    assert entry is not None and (s.citekey in text or entry.record.title in text), s.citekey
                if isinstance(s, MetricSpan):
                    m = ctx.metric(s.run_id, s.name, s.split)
                    assert m is not None and f"{m.value:.4f}"[:5] in text, f"metric {s.name} not rendered from ctx"

    def test_final_refuses_unresolved(self) -> None:
        snap = self.sample_snapshot()
        broken = replace(snap, blocks=(*snap.blocks, Block("paragraph", (TextSpan("see "), CiteSpan("nosuch2099")))))
        with tempfile.TemporaryDirectory() as td:
            res = self._export(broken, Path(td), "final")
            assert any(d.severity == "error" and d.code == "unresolved_citation" for d in res.diagnostics)
