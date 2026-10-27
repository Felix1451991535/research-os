"""Provider, parsing, licensing and deduplication edge cases."""

from dataclasses import replace
from typing import Any

import pytest
from researchos.contracts.datasources import BibRecord, SearchQuery
from researchos.contracts.http import HttpResponse
from researchos.contracts.importers import FileImportRequest
from researchos.sdk.testing import FakeHttp, InMemoryKnowledgeWriter, json_response
from test_litsearch_conformance import FIX, http

from researchos_plugin_litsearch.importers import BibtexImporter, RisImporter, SelectionImporter
from researchos_plugin_litsearch.json_sources import CrossrefSource, OpenAlexSource, SemanticScholarSource
from researchos_plugin_litsearch.normalize import arxiv, doi, group_results
from researchos_plugin_litsearch.xml_sources import ArxivSource, PubMedSource, xml


@pytest.mark.parametrize("cls,suffix", [(RisImporter, "ris"), (BibtexImporter, "bib")])
def test_file_parsers(cls: Any, suffix: str) -> None:
    records = cls().parse_file(FileImportRequest("sample." + suffix, (FIX / ("sample." + suffix)).read_bytes()))
    assert records[0].identifiers == (("doi", "10.5555/test"),)
    assert records[0].year is None
    assert records[0].title == "Synthetic bilingual study"


@pytest.mark.parametrize("cls", [CrossrefSource, OpenAlexSource, SemanticScholarSource, ArxivSource, PubMedSource])
def test_http_failure(cls: Any) -> None:
    source = cls()
    fake = FakeHttp({source.api: json_response(source.api, "{}", status=503)}, source.spec.hosts)
    page = source.search(SearchQuery("test"), fake, {"semantic_scholar_api_key": "SYNTHETIC"})
    assert not page.records and page.warnings


def test_semantic_disabled_without_key() -> None:
    fake = FakeHttp({}, ())
    assert not SemanticScholarSource().search(SearchQuery("test"), fake, {}).records
    assert not fake.calls


@pytest.mark.parametrize("data", [b"TY  - JOUR\nTI  - incomplete", b"garbage", b"\xff"])
def test_bad_ris(data: bytes) -> None:
    with pytest.raises((ValueError, UnicodeError)):
        RisImporter().parse_file(FileImportRequest("bad.ris", data))


def test_entities_refused() -> None:
    with pytest.raises(ValueError):
        xml('<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><x>&x;</x>')


def test_unknown_and_normalized() -> None:
    assert doi("HTTPS://DOI.ORG/10.5555/ABC") == "10.5555/abc"
    assert arxiv("arXiv:1234.12345v3") == "1234.12345"
    assert OpenAlexSource().search(SearchQuery("test"), http(), {}).records[0].year is None


def test_dedupe_preserves_provenance_and_conflicts() -> None:
    a = BibRecord("a", "1", "Same title", identifiers=(("doi", "10.5555/a"),))
    b = replace(a, source="b", source_id="2")
    c = replace(a, source="c", identifiers=(("doi", "10.5555/b"),))
    groups = group_results((a, b, c))
    assert groups[0].records == (a, b) and groups[1].records == (c,)


@pytest.mark.parametrize(
    "license_name,provenance,expected", [(None, None, 0), ("CC-BY-4.0", None, 0), ("CC-BY-4.0", "synthetic license", 1)]
)
def test_pdf_license_and_idempotency(license_name: str | None, provenance: str | None, expected: int) -> None:
    url = "https://arxiv.org/pdf/synthetic"
    fake = FakeHttp({url: HttpResponse(200, {}, b"%PDF-1.4 SYNTHETIC", url)}, ("arxiv.org",))
    record = BibRecord("fixture", "1", "Synthetic", oa_pdf_url=url, license=license_name, license_source=provenance)
    writer = InMemoryKnowledgeWriter()
    importer = SelectionImporter()
    importer.import_records((record,), writer, fake, {})
    importer.import_records((record,), writer, fake, {})
    assert len(writer.originals) == expected and len(writer.works) == 1


def test_pdf_redirect_denied_metadata_retained() -> None:
    url = "https://arxiv.org/pdf/synthetic"
    fake = FakeHttp({url: HttpResponse(200, {}, b"%PDF-1.4", "http://127.0.0.1/x")}, ("arxiv.org",))
    r = BibRecord("fixture", "1", "Synthetic", oa_pdf_url=url, license="CC-BY-4.0", license_source="explicit")
    writer = InMemoryKnowledgeWriter()
    assert SelectionImporter().import_records((r,), writer, fake, {})[0].work_id
    assert not writer.originals


def test_query_filters_not_silently_ignored() -> None:
    with pytest.raises(ValueError):
        CrossrefSource().search(SearchQuery("test", filters={"unknown": "x"}), http(), {})


@pytest.mark.parametrize(
    "cls,body",
    [
        (CrossrefSource, '{"message":{"items":[]}}'),
        (OpenAlexSource, '{"results":[],"meta":{}}'),
        (SemanticScholarSource, '{"data":[]}'),
        (PubMedSource, '{"esearchresult":{"idlist":[],"count":"0"}}'),
        (ArxivSource, '<feed xmlns="http://www.w3.org/2005/Atom"/>'),
    ],
)
def test_empty_pages(cls: Any, body: str) -> None:
    source = cls()
    fake = FakeHttp({source.api: json_response(source.api, body)}, source.spec.hosts)
    result = source.search(SearchQuery("empty"), fake, {"semantic_scholar_api_key": "SYNTHETIC"})
    assert not result.records and result.next_cursor is None


@pytest.mark.parametrize("cls", [CrossrefSource, OpenAlexSource, SemanticScholarSource, PubMedSource, ArxivSource])
def test_malformed_response_is_error(cls: Any) -> None:
    source = cls()
    fake = FakeHttp({source.api: json_response(source.api, "not a provider response")}, source.spec.hosts)
    from xml.etree.ElementTree import ParseError

    with pytest.raises((ValueError, ParseError)):
        source.search(SearchQuery("invalid"), fake, {"semantic_scholar_api_key": "SYNTHETIC"})


def test_crossref_cursor_forwarding() -> None:
    fake = http()
    CrossrefSource().search(SearchQuery("test", page_size=1, cursor="opaque+cursor"), fake, {})
    assert "cursor=opaque%2Bcursor" in fake.calls[0] and "rows=1" in fake.calls[0]


def test_ris_unicode_and_continuation() -> None:
    raw = "TY  - JOUR\nTI  - \u4f20\u611f\u5668\n  extended title\nER  - \n".encode()
    record = RisImporter().parse_file(FileImportRequest("cnki.ris", raw))[0]
    assert record.title == "\u4f20\u611f\u5668 extended title"
    assert record.year is None and not record.identifiers


def test_host_denial_is_not_retried() -> None:
    from researchos.contracts.http import HttpDenied

    fake = FakeHttp({}, ())
    with pytest.raises(HttpDenied):
        CrossrefSource().search(SearchQuery("test"), fake, {})
    assert not fake.calls


def test_one_malformed_record_does_not_discard_the_page() -> None:
    source = CrossrefSource()
    body = (
        '{"message":{"items":[{"DOI":"10.1/OK","title":["Kept"]},{"DOI":"10.1/bad"},'
        '{"DOI":"10.1/ok2","title":["Also kept"]}],"total-results":3}}'
    )
    fake = FakeHttp({source.api: json_response(source.api, body)}, source.spec.hosts)
    page = source.search(SearchQuery("x"), fake, {})
    assert [r.title for r in page.records] == ["Kept", "Also kept"]
    assert page.warnings == ("Crossref: skipped 1 malformed record(s)",)


@pytest.mark.parametrize(
    "cls,expect",
    [
        (CrossrefSource, ["query.author=Ada+Lovelace", "filter=from-pub-date%3A2020"]),
        (OpenAlexSource, ["filter=raw_author_name.search%3AAda+Lovelace%2Cfrom_publication_date%3A2020-01-01"]),
    ],
)
def test_author_and_from_year_filters(cls: Any, expect: list[str]) -> None:
    source = cls()
    body = '{"message":{"items":[]}}' if cls is CrossrefSource else '{"results":[],"meta":{}}'
    fake = FakeHttp({source.api: json_response(source.api, body)}, source.spec.hosts)
    source.search(SearchQuery("Ada Lovelace", filters={"author": "Ada Lovelace", "from_year": "2020"}), fake, {})
    assert all(e in fake.calls[0] for e in expect), fake.calls[0]
    with pytest.raises(ValueError):
        source.search(SearchQuery("x", filters={"from_year": "20"}), fake, {})


def test_semantic_scholar_reports_unsupported_author_filter() -> None:
    page = SemanticScholarSource().search(SearchQuery("Ada", filters={"author": "Ada"}), http(), {})
    assert not page.records and "not supported" in page.warnings[0]


def test_pubmed_external_doctype_accepted_internal_subset_refused() -> None:
    """Live finding (2026-10-13): real EFetch XML starts with an external DOCTYPE; it must parse."""
    real = (
        '<?xml version="1.0" ?>\n<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January '
        '2025//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">\n<PubmedArticleSet/>'
    )
    assert xml(real).tag == "PubmedArticleSet"
    for bad in ("<!DOCTYPE x [<!ELEMENT x ANY>]><x/>", '<!DOCTYPE x SYSTEM "a.dtd"><!ENTITY y "z"><x/>'):
        with pytest.raises(ValueError):
            xml(bad)
