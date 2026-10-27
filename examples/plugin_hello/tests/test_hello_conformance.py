"""The reference plugin passes every core conformance suite fully offline (recorded/synthetic fixtures only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from researchos_plugin_hello import template_package
from researchos_plugin_hello.dataset import SyntheticWindows
from researchos_plugin_hello.exporter import PlainTextExporter
from researchos_plugin_hello.importer import OpenAccessImporter, RisFileImporter
from researchos_plugin_hello.source import API, CrossrefLikeSource
from researchos_plugin_hello.tool import word_count

from researchos.contracts.datasources import BibRecord, SearchQuery
from researchos.contracts.exporters import (
    Block,
    CitationEntry,
    CiteSpan,
    MetricSpan,
    MetricValue,
    ReportSnapshot,
    TextSpan,
)
from researchos.contracts.http import HttpResponse
from researchos.sdk.conformance import (
    BibliographicSourceConformance,
    DatasetProviderConformance,
    ExporterConformance,
    ImporterConformance,
    TemplateConformance,
    ToolConformance,
)
from researchos.sdk.testing import FakeExportContext, FakeHttp, json_response

FIX = Path(__file__).parents[1] / "fixtures"
PDF = HttpResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.4 tiny", "https://api.crossref.org/pdf/x")


def http() -> FakeHttp:
    return FakeHttp(
        {API: json_response(API, (FIX / "crossref_search.json").read_text()), "https://api.crossref.org/pdf/": PDF},
        ("api.crossref.org",),
    )


class TestSource(BibliographicSourceConformance):
    def make_source(self) -> CrossrefLikeSource:
        return CrossrefLikeSource()

    def make_http(self) -> FakeHttp:
        return http()

    def sample_query(self) -> SearchQuery:
        return SearchQuery("activity recognition", page_size=2)

    def test_plugin_specific_normalization(self) -> None:
        page = self._search()
        assert page.records[0].identifiers == (("doi", "10.5555/hello.0001"),)
        assert page.records[0].kind == "review" and page.records[0].license == "CC-BY-4.0"
        assert page.records[1].year is None and page.records[1].license is None  # unknown stays unknown


class TestDataset(DatasetProviderConformance):
    def make_provider(self) -> SyntheticWindows:
        return SyntheticWindows()

    def make_http(self) -> FakeHttp:
        return FakeHttp({}, ())


class TestImporter(ImporterConformance):
    def make_importer(self) -> OpenAccessImporter:
        return OpenAccessImporter()

    def sample_records(self) -> tuple[BibRecord, ...]:
        return CrossrefLikeSource().search(SearchQuery("x", page_size=2), http(), {}).records

    def make_http(self) -> FakeHttp:
        return http()


def test_ris_parser() -> None:
    ris = b"TY  - JOUR\nTI  - A title\nAU  - Zhang San\nPY  - 2023\nDO  - 10.5555/ABC\nER  - \n"
    from researchos.contracts.importers import FileImportRequest

    recs = RisFileImporter().parse_file(FileImportRequest("x.ris", ris))
    assert recs[0].title == "A title" and recs[0].year == 2023 and recs[0].identifiers == (("doi", "10.5555/abc"),)


class TestExporter(ExporterConformance):
    def make_exporter(self) -> PlainTextExporter:
        return PlainTextExporter()

    def sample_snapshot(self) -> ReportSnapshot:
        return ReportSnapshot(
            "doc1",
            1,
            "Demo",
            "en",
            (
                Block(
                    "paragraph", (TextSpan("Subject-wise splits matter "), CiteSpan("test2020subject"), TextSpan("."))
                ),
                Block("paragraph", (TextSpan("Macro-F1 was "), MetricSpan("run1", "macro_f1", "search_val"))),
            ),
        )

    def make_context(self, out_dir: Path) -> FakeExportContext:
        rec = BibRecord("lib", "w1", "Subject-wise validation in HAR", ("Bo Test",), 2020)
        return FakeExportContext(
            out_dir,
            {"test2020subject": CitationEntry("test2020subject", rec)},
            {("run1", "macro_f1", "search_val"): MetricValue(0.9124, None, "recomputed")},
        )


class TestTool(ToolConformance):
    def make_binding(self) -> Any:
        return word_count

    def sample_args(self) -> dict[str, Any]:
        return {"text": "one two three"}


def _arrays(groups: list[str], seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    n = len(groups) * 3
    y = np.array([i % 3 for i in range(n)])
    return {
        "X": (rng.normal(size=(n, 4, 2)) + y[:, None, None]).astype("float32"),
        "y": y,
        "group": np.array([g for g in groups for _ in range(3)]),
        "sample_id": np.array([f"{g}-{i}" for g in groups for i in range(3)]),
    }


class TestTemplate(TemplateConformance):
    def template_root(self) -> Path:
        return template_package().root

    def params(self) -> dict[str, Any]:
        return {"shrink": 0.0}

    def split_arrays(self) -> dict[str, dict[str, Any]]:
        return {"train": _arrays(["s0", "s1", "s2", "s3"], 0), "search_val": _arrays(["s4", "s5"], 1)}


def test_manifest_is_valid_json_shape() -> None:
    from researchos_plugin_hello import MANIFEST

    assert json.loads(MANIFEST.model_dump_json())["capabilities"] == ["network:api.crossref.org"]
