"""zotero_csljson / zotero_rdf (plain pytest; FileImporters have no core conformance suite)."""

from __future__ import annotations

import pytest
from researchos.contracts.importers import FileImportRequest

from helpers import FIX, csl_records, no_empty_strings
from researchos_plugin_zotero_import import csljson, ids, rdf
from researchos_plugin_zotero_import.csljson import ZoteroCslJsonImporter
from researchos_plugin_zotero_import.rdf import ZoteroRdfImporter


def _rdf_bytes() -> bytes:
    return (FIX / "zotero_export.rdf").read_bytes()


def test_csljson_roundtrip_fields() -> None:
    recs = csl_records()
    assert len(recs) == 6  # 7 items; the one without a title is skipped
    r = recs[0]
    assert (r.source, r.source_id) == ("zotero_csljson", "users/1234567/ABCD2345")
    assert r.identifiers == (
        ("zotero", "users/1234567/ABCD2345"),
        ("doi", "10.5555/syn.0001"),
        ("pmid", "34000001"),
        ("pmcid", "PMC8000001"),
    )
    assert r.authors == ("Wei Zhang", "Anna Müller") and r.year == 2021 and r.kind == "article"
    assert (r.venue, r.abstract, r.url) == (
        "Journal of Synthetic Cardiology",
        "Synthetic abstract.",
        "https://example.org/syn1",
    )
    assert r.oa_pdf_url is None and r.license is None and r.license_source is None
    assert all(no_empty_strings(x) for x in recs)


def test_csljson_unicode_cjk_arxiv_and_raw_dates() -> None:
    r = csl_records()[1]
    assert r.title == "心电图深度学习综述（合成测试条目）" and r.authors == ("李明",)
    assert r.year == 2023 and ("arxiv", "2301.01234") in r.identifiers


def test_csljson_group_isbns_local_library_and_citekeys() -> None:
    book, local, citekey = csl_records()[2:5]
    assert (
        book.identifiers == (("zotero", "groups/987654/JKLM2345"), ("isbn", "9780306406157"), ("isbn", "0306406152"))
        and book.year == 2019
        and book.kind == "book"
    )
    assert local.source_id == "http://zotero.org/users/local/AbCd1234/items/NPQR2345" and local.identifiers == ()
    assert local.year is None and local.abstract is None and local.authors == ("Synthetic Consortium",)
    assert citekey.source_id == "smith2020citekey" and citekey.identifiers == (("doi", "10.5555/syn.0005"),)


def test_csljson_parsed_extras_kept_on_item() -> None:
    items = csljson.parse_items((FIX / "zotero_export.csl.json").read_bytes())
    assert items[0].tags == ("ECG", "HRV") and items[0].key == "ABCD2345" and items[6].title is None


@pytest.mark.parametrize("data", [b"not json", b"{}", b"[1, 2]", b"\xff\xfe[", b'{"items": []}'])
def test_csljson_malformed_input_is_an_error(data: bytes) -> None:
    with pytest.raises(csljson.CslJsonError):
        ZoteroCslJsonImporter().parse_file(FileImportRequest("bad.json", data))


def test_csljson_empty_fields_are_none() -> None:
    data = b'[{"id": "x", "title": "T", "DOI": " ", "container-title": "", "author": [], "issued": {}}]'
    (r,) = ZoteroCslJsonImporter().parse_file(FileImportRequest("x.json", data))
    assert r.venue is None and r.year is None and r.identifiers == () and r.authors == ()


def test_rdf_roundtrip_fields() -> None:
    recs = ZoteroRdfImporter().parse_file(FileImportRequest("export.rdf", _rdf_bytes()))
    assert [r.source_id for r in recs] == ["#item_1", "urn:isbn:9780306406157", "#item_5"]
    a = recs[0]
    assert a.title == "Seizure detection from scalp EEG: a synthetic RDF test item"
    assert a.authors == ("Ada Okafor", "Ken Tanaka") and a.venue == "Synthetic Journal of Signal Processing"
    assert a.year == 2022 and a.identifiers == (("doi", "10.5555/syn.rdf.0001"), ("pmid", "35000002"))
    assert a.url == "https://example.org/rdf1" and a.license == "CC-BY-4.0" and a.kind == "article"
    assert all(no_empty_strings(x) for x in recs)


def test_rdf_cjk_containers_years_and_extras() -> None:
    items = rdf.parse_items(_rdf_bytes())
    article, book, section, untitled = items
    assert untitled.title is None and untitled.item_type == "webpage"  # dropped later by parse_file
    assert article.tags == ("EEG", "Seizure detection") and article.collections == ("Thesis/EEG",)
    assert article.notes == ("Reviewer note: check the patient-level split",)
    assert article.attachments[0].content_type == "application/pdf"
    assert book.title == "脑电信号处理（合成测试）" and book.isbns == ("9780306406157",) and book.year == 2018
    assert section.container == book.title and section.year is None and section.collections == ("Thesis",)


@pytest.mark.parametrize(
    "data",
    [
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]><rdf:RDF '
        b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">&lol2;</rdf:RDF>',
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><r>&xxe;</r>',
        b"<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'><unclosed></rdf:RDF>",
        b"<root/>",
        b"",
    ],
)
def test_rdf_unsafe_or_malformed_xml_is_refused(data: bytes) -> None:
    with pytest.raises(rdf.RdfError):
        ZoteroRdfImporter().parse_file(FileImportRequest("bad.rdf", data))


@pytest.mark.parametrize(
    ("raw", "doi"),
    [
        ("10.1000/ABC", "10.1000/abc"),
        ("https://doi.org/10.1000/X.Y", "10.1000/x.y"),
        ("http://dx.doi.org/10.1000/x", "10.1000/x"),
        ("doi:10.1000/x", "10.1000/x"),
        ("DOI 10.1000/x", "10.1000/x"),
        ("  10.1000/x  ", "10.1000/x"),
        ("not a doi", None),
        ("", None),
        (None, None),
        ("11.1000/x", None),
    ],
)
def test_doi_normalisation(raw: str | None, doi: str | None) -> None:
    assert ids.norm_doi(raw) == doi


def test_other_identifier_normalisation() -> None:
    assert ids.norm_pmid("PMID: 123") == "123" and ids.norm_pmid("0123") is None and ids.norm_pmid("x") is None
    assert ids.norm_pmcid("pmc77") == "PMC77" and ids.norm_pmcid("77") == "PMC77" and ids.norm_pmcid("PMCx") is None
    assert ids.norm_arxiv("arXiv:2301.01234v3") == "2301.01234"
    assert ids.norm_arxiv("https://arxiv.org/pdf/2301.01234v1.pdf") == "2301.01234"
    assert ids.norm_arxiv("hep-th/9901001") == "hep-th/9901001" and ids.norm_arxiv("1234") is None
    assert ids.norm_isbns("ISBN 978-0-306-40615-7") == ["9780306406157"] and ids.norm_isbns("978-0-306-40615-8") == []


def test_year_and_license_rules() -> None:
    assert ids.year_of("2020-05-01") == 2020 and ids.year_of("May 2020") == 2020
    assert ids.year_of("2019-2020") is None and ids.year_of("n.d.") is None and ids.year_of("0999") is None
    assert ids.license_of("CC BY 4.0") == "CC-BY-4.0" and ids.license_of("CC BY-SA 4.0") == "CC-BY-SA-4.0"
    assert ids.license_of("CC BY-NC 4.0") is None and ids.license_of("All rights reserved") is None
