"""stats_report behaviour beyond the conformance suite."""

from __future__ import annotations

import csv
import io
from dataclasses import replace
from pathlib import Path

from researchos.contracts.exporters import Block, CitationEntry, CiteSpan, MetricSpan, MetricValue, TextSpan
from researchos.sdk.testing import FakeExportContext

from researchos_plugin_diag_stats.report import CSV_COLUMNS, StatsReportExporter
from test_conformance import METRICS, SYNTHETIC, make_context, sample_snapshot

EXP = StatsReportExporter()


def _with(*blocks: Block) -> object:
    snap = sample_snapshot()
    return replace(snap, blocks=(*snap.blocks, *blocks))


def test_markdown_and_csv_content(tmp_path: Path) -> None:
    res = EXP.export(sample_snapshot(), make_context(tmp_path), {})
    assert [f.rel_path for f in res.files] == ["stats_appendix.md", "stats_appendix.csv"]
    assert not res.diagnostics
    md = (tmp_path / "stats_appendix.md").read_text(encoding="utf-8")
    assert "0.9123 [M2]" in md and "48.0000 subjects [M1]" in md and "[synth2026]" in md
    assert "AUC \\| ROC" in md and "- [x] Every reported number resolves" in md
    assert "Synthetic reference record used only in tests" in md and "(2026)" in md
    assert "STARD 2015" in md and "need a human check" in md
    rows = list(csv.DictReader(io.StringIO((tmp_path / "stats_appendix.csv").read_text(encoding="utf-8"))))
    assert tuple(rows[0]) == CSV_COLUMNS
    metric_rows = [r for r in rows if r["kind"] == "metric"]
    assert len(metric_rows) == len(METRICS) and {r["status"] for r in rows} == {"resolved"}
    assert {float(r["value"]) for r in metric_rows} == {m.value for m in METRICS.values()}  # full precision
    assert [r["citekey"] for r in rows if r["kind"] == "citation"] == ["synth2026"]


def test_final_with_unresolved_metric_errors_and_writes_nothing(tmp_path: Path) -> None:
    snap = _with(Block("paragraph", (MetricSpan("run_synth", "nosuch", "test"),)))
    res = EXP.export(snap, make_context(tmp_path), {})  # type: ignore[arg-type]
    assert res.files == () and list(tmp_path.iterdir()) == []
    assert any(d.severity == "error" and d.code == "unresolved_metric" for d in res.diagnostics)


def test_draft_warns_and_marks_unresolved(tmp_path: Path) -> None:
    snap = _with(
        Block("paragraph", (CiteSpan("nosuch2099"), MetricSpan("r", "m", "test"))),
        Block("figure", artifact_id="fig_missing", caption=(TextSpan("ROC"),)),
    )
    ctx = make_context(tmp_path)
    ctx.mode = "draft"
    res = EXP.export(snap, ctx, {})  # type: ignore[arg-type]
    codes = {(d.severity, d.code) for d in res.diagnostics}
    assert codes == {
        ("warning", "unresolved_citation"),
        ("warning", "unresolved_metric"),
        ("warning", "missing_artifact"),
    }
    md = (tmp_path / "stats_appendix.md").read_text(encoding="utf-8")
    assert "[unresolved:nosuch2099]" in md and "[missing:r/m/test]" in md and "- [ ] Every citation" in md
    assert "| M5 | unresolved |" in md


def test_self_reported_numbers_are_flagged(tmp_path: Path) -> None:
    ctx = FakeExportContext(
        out_dir=tmp_path,
        citations={"synth2026": CitationEntry("synth2026", SYNTHETIC)},
        metrics={**METRICS, ("run_synth", "auc", "test"): MetricValue(0.95, None, "self_reported")},
    )
    res = EXP.export(sample_snapshot(), ctx, {})
    assert res.files and [d.code for d in res.diagnostics] == ["unverified_metric"]
    assert res.diagnostics[0].severity == "warning"
    md = (tmp_path / "stats_appendix.md").read_text(encoding="utf-8")
    assert "- [ ] Numbers are recomputed or invariant-checked (3 of 4;" in md and "self_reported: 1" in md


def test_non_finite_metric_is_an_error_in_final(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    ctx.metrics = {**METRICS, ("run_synth", "auc", "test"): MetricValue(float("nan"), None, "recomputed")}
    res = EXP.export(sample_snapshot(), ctx, {})
    assert res.files == () and [d.code for d in res.diagnostics] == ["nonfinite_metric"]


def test_csv_formula_injection_is_neutralised(tmp_path: Path) -> None:
    metrics = {("=cmd()", "auc", "test"): MetricValue(0.5, None, "recomputed")}
    snap = replace(sample_snapshot(), blocks=(Block("paragraph", (MetricSpan("=cmd()", "auc", "test"),)),))
    res = EXP.export(snap, FakeExportContext(out_dir=tmp_path, metrics=metrics), {})
    assert res.files
    text = (tmp_path / "stats_appendix.csv").read_text(encoding="utf-8")
    assert "'=cmd()" in text and ",=cmd()" not in text


def test_options_validation_and_confinement(tmp_path: Path) -> None:
    for bad in ({"basename": "../escape"}, {"basename": "a/b"}, {"decimals": 2}, {"colour": "red"}):
        res = EXP.export(sample_snapshot(), make_context(tmp_path), bad)
        assert res.files == () and res.diagnostics[0].code == "invalid_options", bad
    res = EXP.export(sample_snapshot(), make_context(tmp_path / "out"), {"basename": "appendix_v2", "decimals": 6})
    assert {f.rel_path for f in res.files} == {"appendix_v2.md", "appendix_v2.csv"}
    assert "0.912345 [M2]" in (tmp_path / "out" / "appendix_v2.md").read_text(encoding="utf-8")
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == ["appendix_v2.csv", "appendix_v2.md"]


def test_unsupported_kind(tmp_path: Path) -> None:
    res = EXP.export(replace(sample_snapshot(), kind="slides"), make_context(tmp_path), {})
    assert res.files == () and res.diagnostics[0].code == "unsupported_kind"
