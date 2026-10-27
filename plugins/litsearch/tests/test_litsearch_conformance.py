"""Every registered source/importer has its matching SDK suite (files use plain pytest)."""

from pathlib import Path
from typing import Any

from researchos.contracts.datasources import BibliographicSource, BibRecord, SearchQuery
from researchos.sdk.conformance import BibliographicSourceConformance, ImporterConformance
from researchos.sdk.testing import FakeHttp, json_response

from researchos_plugin_litsearch.importers import SelectionImporter
from researchos_plugin_litsearch.json_sources import CrossrefSource, OpenAlexSource, SemanticScholarSource
from researchos_plugin_litsearch.xml_sources import ArxivSource, PubMedSource

FIX = Path(__file__).parents[1] / "fixtures"


def http() -> FakeHttp:
    routes = {
        CrossrefSource.api: "crossref.json",
        OpenAlexSource.api: "openalex.json",
        SemanticScholarSource.api: "semantic.json",
        ArxivSource.api: "arxiv.xml",
        PubMedSource.api + "esearch.fcgi": "pubmed.json",
        PubMedSource.api + "efetch.fcgi": "pubmed.xml",
    }
    return FakeHttp(
        {url: json_response(url, (FIX / filename).read_text()) for url, filename in routes.items()},
        (
            "api.crossref.org",
            "api.openalex.org",
            "api.semanticscholar.org",
            "export.arxiv.org",
            "eutils.ncbi.nlm.nih.gov",
        ),
    )


class TestCrossref(BibliographicSourceConformance):
    def make_source(self) -> BibliographicSource:
        return CrossrefSource()

    def make_http(self) -> FakeHttp:
        return http()

    def sample_query(self) -> SearchQuery:
        return SearchQuery("synthetic", page_size=2)


class TestOpenAlex(TestCrossref):
    def make_source(self) -> BibliographicSource:
        return OpenAlexSource()


class TestSemanticScholar(TestCrossref):
    def make_source(self) -> BibliographicSource:
        return SemanticScholarSource()

    def settings(self) -> dict[str, Any]:
        return {"semantic_scholar_api_key": "SYNTHETIC-NON-CREDENTIAL"}


class TestPubMed(TestCrossref):
    def make_source(self) -> BibliographicSource:
        return PubMedSource()


class TestArxiv(TestCrossref):
    def make_source(self) -> BibliographicSource:
        return ArxivSource()


class TestSelection(ImporterConformance):
    def make_importer(self) -> SelectionImporter:
        return SelectionImporter()

    def sample_records(self) -> tuple[BibRecord, ...]:
        return CrossrefSource().search(SearchQuery("synthetic"), http(), {}).records

    def make_http(self) -> FakeHttp:
        return http()
