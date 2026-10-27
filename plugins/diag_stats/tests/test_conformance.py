"""researchos.sdk conformance suites: ToolConformance per tool, ExporterConformance for stats_report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from researchos.contracts.datasources import BibRecord
from researchos.contracts.exporters import (
    Block,
    CitationEntry,
    CiteSpan,
    Exporter,
    MetricSpan,
    MetricValue,
    ReportSnapshot,
    TextSpan,
)
from researchos.contracts.gateway import ToolBinding
from researchos.sdk.conformance import ExporterConformance, ToolConformance
from researchos.sdk.testing import FakeExportContext

import researchos_plugin_diag_stats.bootstrap as bootstrap
import researchos_plugin_diag_stats.diag_metrics as diag_metrics
import researchos_plugin_diag_stats.multiplicity as multiplicity
import researchos_plugin_diag_stats.paired as paired
import researchos_plugin_diag_stats.roc as roc
from helpers import GOLDEN
from researchos_plugin_diag_stats.report import StatsReportExporter


class TestDiagMetricsTool(ToolConformance):
    def make_binding(self) -> ToolBinding:
        return diag_metrics.BINDING

    def sample_args(self) -> dict[str, Any]:
        return {"tp": 45, "fp": 12, "fn": 5, "tn": 88, "confidence": 0.95}


class TestDiagMetricsConfusionTool(ToolConformance):
    def make_binding(self) -> ToolBinding:
        return diag_metrics.BINDING

    def sample_args(self) -> dict[str, Any]:  # largest allowed matrix: output must stay bounded
        k = diag_metrics.MAX_CLASSES
        return {
            "confusion": [[10 if i == j else 1 for j in range(k)] for i in range(k)],
            "labels": [f"c{i:02d}" for i in range(k)],
        }


class TestRocAucTool(ToolConformance):
    def make_binding(self) -> ToolBinding:
        return roc.BINDING

    def sample_args(self) -> dict[str, Any]:
        r = GOLDEN["roc"]
        return {"y_true": r["y_true"], "scores": r["scores"], "scores_b": r["scores_b"]}


class TestPairedTestsTool(ToolConformance):
    def make_binding(self) -> ToolBinding:
        return paired.BINDING

    def sample_args(self) -> dict[str, Any]:
        return {"test": "fleiss_kappa", "table": GOLDEN["fleiss"]["table"], "resamples": 200, "seed": 7}


class TestClusterBootstrapTool(ToolConformance):
    def make_binding(self) -> ToolBinding:
        return bootstrap.BINDING

    def sample_args(self) -> dict[str, Any]:
        r = GOLDEN["roc"]
        return {
            "metric": "auc",
            "y_true": r["y_true"],
            "scores": r["scores"],
            "clusters": [f"s{i // 3}" for i in range(len(r["y_true"]))],
            "resamples": 500,
            "seed": 11,
        }


class TestMultiplicityTool(ToolConformance):
    def make_binding(self) -> ToolBinding:
        return multiplicity.BINDING

    def sample_args(self) -> dict[str, Any]:  # largest allowed family
        return {
            "p_values": [(i + 1) / (multiplicity.MAX_P + 1) for i in range(multiplicity.MAX_P)],
            "labels": [f"comparison_{i:03d}" for i in range(multiplicity.MAX_P)],
        }


SYNTHETIC = BibRecord(
    source="test",
    source_id="synthetic-1",
    title="Synthetic reference record used only in tests",
    authors=("Tester A",),
    year=2026,
)
METRICS = {
    ("run_synth", "sensitivity", "test"): MetricValue(0.912345, None, "recomputed"),
    ("run_synth", "specificity", "test"): MetricValue(0.876543, None, "recomputed"),
    ("run_synth", "auc", "test"): MetricValue(0.95, None, "invariant_checked"),
    ("run_synth", "n_subjects", "test"): MetricValue(48.0, "subjects", "recomputed"),
}


def sample_snapshot() -> ReportSnapshot:
    m = {name: MetricSpan("run_synth", name, "test") for (_, name, _) in METRICS}
    return ReportSnapshot(
        doc_id="doc_synth",
        revision=3,
        title="Synthetic evaluation",
        language="en",
        blocks=(
            Block("heading", (TextSpan("Diagnostic accuracy"),), level=1),
            Block(
                "paragraph",
                (
                    TextSpan("On the test split ("),
                    m["n_subjects"],
                    TextSpan("), sensitivity was "),
                    m["sensitivity"],
                    TextSpan(" and specificity "),
                    m["specificity"],
                    TextSpan(", reported following "),
                    CiteSpan("synth2026"),
                    TextSpan("."),
                ),
            ),
            Block(
                "table", rows=(((TextSpan("Metric"),), (TextSpan("Value"),)), ((TextSpan("AUC | ROC"),), (m["auc"],)))
            ),
            Block("list", (TextSpan("Patient-level split; no subject in two splits."),)),
        ),
    )


def make_context(out_dir: Path) -> FakeExportContext:
    return FakeExportContext(
        out_dir=out_dir, citations={"synth2026": CitationEntry("synth2026", SYNTHETIC)}, metrics=METRICS
    )


class TestStatsReportExporter(ExporterConformance):
    def make_exporter(self) -> Exporter:
        return StatsReportExporter()

    def sample_snapshot(self) -> ReportSnapshot:
        return sample_snapshot()

    def make_context(self, out_dir: Path) -> FakeExportContext:
        return make_context(out_dir)
