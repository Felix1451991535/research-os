"""Both registered exporters run the full SDK exporter conformance suite."""

from pathlib import Path

from docx import Document
from researchos.contracts.datasources import BibRecord
from researchos.contracts.exporters import (
    Block,
    CitationEntry,
    CiteSpan,
    EvidenceRef,
    Exporter,
    ExportResult,
    MetricSpan,
    MetricValue,
    ReportSnapshot,
    TextSpan,
)
from researchos.sdk.conformance import ExporterConformance
from researchos.sdk.testing import FakeExportContext

from researchos_plugin_report_export.exporters import DocxExporter, LatexExporter
from researchos_plugin_report_export.slides import SlidesExporter


def snapshot() -> ReportSnapshot:
    return ReportSnapshot(
        "synthetic",
        1,
        "Synthetic bilingual report",
        "mixed",
        (
            Block("heading", (TextSpan("Results"),)),
            Block(
                "paragraph",
                (TextSpan("Synthetic result "), CiteSpan("test2020"), MetricSpan("run", "accuracy", "search_val")),
                claim_id="claim-1",
            ),
            Block(
                "table",
                rows=(
                    ((TextSpan("Metric"),), (TextSpan("Value"),)),
                    ((TextSpan("Accuracy"),), (MetricSpan("run", "accuracy", "search_val"),)),
                ),
            ),
            Block("list", (TextSpan("Synthetic item"),)),
        ),
    )


def context(out: Path) -> FakeExportContext:
    record = BibRecord("synthetic", "1", "Synthetic source", ("Test Author",), 2020)
    evidence = EvidenceRef("evidence-1", "version-1", 0, "Synthetic quote", "untrusted")
    return FakeExportContext(
        out,
        {"test2020": CitationEntry("test2020", record, (evidence,))},
        {("run", "accuracy", "search_val"): MetricValue(0.9124, None, "recomputed")},
    )


class TestLatex(ExporterConformance):
    def make_exporter(self) -> Exporter:
        return LatexExporter()

    def sample_snapshot(self) -> ReportSnapshot:
        return snapshot()

    def make_context(self, out_dir: Path) -> FakeExportContext:
        return context(out_dir)


class TestDocx(TestLatex):
    def make_exporter(self) -> Exporter:
        return DocxExporter()

    def rendered_text(self, out_dir: Path, result: ExportResult) -> str:
        document = Document(str(out_dir / "report.docx"))
        return "\n".join(p.text for p in document.paragraphs)


class TestSlides(TestLatex):
    def make_exporter(self) -> Exporter:
        return SlidesExporter()

    def rendered_text(self, out_dir: Path, result: ExportResult) -> str:
        from pptx import Presentation

        deck = Presentation(str(out_dir / "report.pptx"))
        return "\n".join(shape.text for slide in deck.slides for shape in slide.shapes if shape.has_text_frame)
