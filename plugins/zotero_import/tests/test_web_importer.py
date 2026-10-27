"""zotero_web: ImporterConformance (FakeHttp + InMemoryKnowledgeWriter) plus pagination, dedupe, idempotency
and secret-handling tests. All API responses are SYNTHETIC fixtures."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, replace
from typing import Any

import pytest
from researchos.contracts.datasources import BibRecord
from researchos.contracts.http import HttpResponse, HttpUnavailable
from researchos.sdk.conformance import ImporterConformance
from researchos.sdk.testing import FakeHttp, InMemoryKnowledgeWriter

from helpers import (
    FAKE_KEY,
    KEYS,
    LIB,
    SETTINGS,
    CountingWriter,
    RecordingHttp,
    csl_records,
    page_url,
    two_page_routes,
    web_record,
)
from researchos_plugin_zotero_import.web import ZoteroWebImporter, next_start


def sample() -> tuple[BibRecord, ...]:
    csl = csl_records()
    # API-fetched (ABCD2345, EFGH6789), local library mapped via settings (NPQR2345), file record written as parsed
    # (citation key), attachment (rejected), deleted item (rejected)
    return (csl[0], csl[1], csl[3], csl[4], web_record("ZZZZ2345"), web_record("WXYZ2345"))


class TestZoteroWeb(ImporterConformance):
    def make_importer(self) -> ZoteroWebImporter:
        return ZoteroWebImporter()

    def sample_records(self) -> tuple[BibRecord, ...]:
        return sample()

    def make_http(self) -> FakeHttp:
        return RecordingHttp(two_page_routes())

    def settings(self) -> dict[str, Any]:
        return dict(SETTINGS)


def run(
    records: tuple[BibRecord, ...],
    http: FakeHttp | None = None,
    writer: InMemoryKnowledgeWriter | None = None,
    settings: dict[str, Any] | None = None,
) -> tuple[list[Any], InMemoryKnowledgeWriter, FakeHttp]:
    http = http or RecordingHttp(two_page_routes())
    writer = writer if writer is not None else CountingWriter()
    res = ZoteroWebImporter().import_records(records, writer, http, dict(SETTINGS if settings is None else settings))
    return res, writer, http


def one_item_http(**data_changes: Any) -> RecordingHttp:
    """Serves only item ABCD2345 (from page 1), optionally with changed data fields."""
    page = json.loads(two_page_routes()[page_url(LIB, KEYS, 0)].body)  # type: ignore[union-attr]
    item = page[0]
    item["data"].update(data_changes)
    item["version"] = item["data"]["version"]
    url = page_url(LIB, ("ABCD2345",), 0)
    return RecordingHttp({url: HttpResponse(200, {"Total-Results": "1"}, json.dumps([item]).encode(), url)})


def test_results_mapping_and_pagination() -> None:
    res, w, http = run(sample())
    assert [r.status for r in res] == ["created", "created", "created", "created", "rejected", "rejected"]
    assert "attachment" in (res[4].reason or "") and "not returned" in (res[5].reason or "")
    assert http.calls == [page_url(LIB, KEYS, 0), page_url(LIB, KEYS, 2)]
    art = w.works[res[0].work_id or ""]
    assert (art.source, art.source_id, art.year, art.venue) == (
        "zotero_web",
        f"{LIB}/ABCD2345",
        2021,
        "Journal of Synthetic Cardiology",
    )
    assert art.authors == ("Wei Zhang", "Anna Müller")  # editors are not authors
    assert art.identifiers == (
        ("zotero", "users/1234567/ABCD2345"),
        ("doi", "10.5555/syn.0001"),
        ("pmid", "34000001"),
        ("pmcid", "PMC8000001"),
    )
    pre = w.works[res[1].work_id or ""]
    assert pre.kind == "preprint" and ("arxiv", "2301.01234") in pre.identifiers and pre.year == 2023
    assert pre.authors == ("李明",) and pre.venue == "arXiv"
    chap = w.works[res[2].work_id or ""]
    assert chap.source_id == f"{LIB}/NPQR2345" and chap.license == "CC-BY-4.0" and chap.year is None
    assert w.works[res[3].work_id or ""].source == "zotero_csljson"  # no key: written exactly as parsed
    assert not w.originals  # attachments are never downloaded or stored


def test_idempotency_keys_use_zotero_key_and_version() -> None:
    _, w, _ = run(sample())
    assert isinstance(w, CountingWriter)
    assert w.upserts[:3] == [
        "zotero:users/1234567/ABCD2345@v12",
        "zotero:users/1234567/EFGH6789@v7",
        "zotero:users/1234567/NPQR2345@v3",
    ]
    assert w.upserts[3].startswith("zotero-file:zotero_csljson:smith2020citekey#")


def test_reimport_is_idempotent() -> None:
    w = CountingWriter()
    first, _, _ = run(sample(), writer=w)
    n_works = len(w.works)
    second, _, _ = run(sample(), writer=w)
    assert len(w.works) == n_works
    assert [r.work_id for r in first] == [r.work_id for r in second]
    assert [r.status for r in second] == ["reused"] * 4 + ["rejected"] * 2


def test_duplicate_items_in_one_import_are_written_once() -> None:
    csl = csl_records()
    assert csl[0] == csl[5]  # the export contains the same item twice
    res, w, _ = run((csl[0], csl[5], web_record("ABCD2345")), http=one_item_http())
    assert isinstance(w, CountingWriter) and len(w.upserts) == 1 and len(w.works) == 1
    assert [r.status for r in res] == ["created", "reused", "reused"] and len({r.work_id for r in res}) == 1


def test_new_version_reuses_the_work_via_doi() -> None:
    w = CountingWriter()
    first, _, _ = run((web_record("ABCD2345"),), http=one_item_http(), writer=w)
    res, _, _ = run((web_record("ABCD2345"),), http=one_item_http(version=13, title="Retitled"), writer=w)
    assert w.upserts == ["zotero:users/1234567/ABCD2345@v12", "zotero:users/1234567/ABCD2345@v13"]
    assert res[0].status == "reused" and res[0].work_id == first[0].work_id and len(w.works) == 1


def test_total_results_pagination_without_link() -> None:
    _, _, http = run(sample(), http=RecordingHttp(two_page_routes(link=False)))
    assert http.calls == [page_url(LIB, KEYS, 0), page_url(LIB, KEYS, 2)]


def test_pagination_link_to_another_host_is_refused() -> None:
    resp = HttpResponse(200, {"Link": '<https://evil.example/items?start=50>; rel="next"'}, b"[]", "x")
    from researchos_plugin_zotero_import.web import ZoteroApiError

    with pytest.raises(ZoteroApiError):
        next_start(resp, 0, 25)
    assert next_start(HttpResponse(200, {"total-results": "30"}, b"[]", "x"), 0, 25) == 25
    assert next_start(HttpResponse(200, {"total-results": "25"}, b"[]", "x"), 0, 25) is None


def test_api_key_only_in_header_never_in_urls_results_errors_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    group = page_url("groups/987654", ("JKLM2345",), 0)

    def unavailable(url: str) -> HttpResponse:
        raise HttpUnavailable(f"upstream echoed key={FAKE_KEY}")

    routes = {
        **two_page_routes(),
        group: HttpResponse(403, {}, f"Invalid key {FAKE_KEY}".encode(), group),
        page_url("groups/111", ("AAAA2345",), 0): unavailable,
    }
    http = RecordingHttp(routes)
    recs = (*sample(), csl_records()[2], BibRecord("zotero_web", "groups/111/AAAA2345", "t"))
    res, w, _ = run(recs, http=http)
    assert all(h.get("Zotero-API-Key") == FAKE_KEY and h.get("Zotero-API-Version") == "3" for h in http.headers)
    blob = json.dumps([asdict(r) for r in res]) + repr(w.works) + " ".join(http.calls) + caplog.text
    assert FAKE_KEY not in blob
    assert "403" in (res[6].reason or "") and "retry" in (res[7].reason or "") and "[redacted]" in (res[7].reason or "")


def test_no_api_key_means_no_key_header() -> None:
    http = RecordingHttp(two_page_routes())
    run(sample(), http=http, settings={"library_type": "user", "library_id": "1234567"})
    assert http.headers and all("Zotero-API-Key" not in h for h in http.headers)


def test_undeclared_host_or_denied_network_rejects_with_reason() -> None:
    res, _, _ = run((web_record("ABCD2345"),), http=RecordingHttp({}, allowed=()))
    assert res[0].status == "rejected" and "not declared" in (res[0].reason or "")


def test_refetch_false_writes_file_records_without_network() -> None:
    csl = csl_records()
    http = RecordingHttp({})
    res, w, _ = run((csl[0], csl[4], web_record("ABCD2345")), http=http, settings={"refetch": False})
    assert http.calls == [] and [r.status for r in res] == ["created", "created", "rejected"]
    assert w.works[res[0].work_id or ""].source == "zotero_csljson"


def test_foreign_records_without_zotero_key_are_rejected() -> None:
    res, _, _ = run((BibRecord("hello_crossref", "10.1/x", "t", identifiers=(("doi", "10.1/x"),)),))
    assert res[0].status == "rejected" and "Zotero" in (res[0].reason or "")


def test_spec_declares_only_zotero_api_and_secret_setting() -> None:
    spec = ZoteroWebImporter.spec
    assert spec.hosts == ("api.zotero.org",) and spec.kind == "bibliographic"
    key = spec.settings_schema["properties"]["api_key"]
    assert key["writeOnly"] is True and key["x-researchos-secret"] is True
    assert "127.0.0.1" not in json.dumps(spec.settings_schema) and "localhost" not in json.dumps(spec.settings_schema)


def test_item_without_title_is_rejected() -> None:
    res, _, _ = run((web_record("ABCD2345"),), http=one_item_http(title=""))
    assert res[0].status == "rejected" and "title" in (res[0].reason or "")


def test_version_bump_without_doi_reuses_the_work_via_zotero_identity() -> None:
    """core 1.3: ('zotero', '<library>/<key>') is a hard, library-scoped identity for the writer."""
    from researchos.sdk.testing import InMemoryKnowledgeWriter

    w = InMemoryKnowledgeWriter()
    rec = BibRecord(
        "zotero_web", "users/1234567/ABCD2345", "No DOI item", identifiers=(("zotero", "users/1234567/ABCD2345"),)
    )
    a, created_a = w.upsert_work(rec, idempotency_key="zotero:users/1234567/ABCD2345@v1")
    b, created_b = w.upsert_work(
        replace(rec, title="No DOI item (edited)"), idempotency_key="zotero:users/1234567/ABCD2345@v2"
    )
    assert created_a and not created_b and a == b
    other_lib = replace(rec, identifiers=(("zotero", "groups/42/ABCD2345"),))
    assert w.upsert_work(other_lib, idempotency_key="zotero:groups/42/ABCD2345@v1")[1]  # other library: new work
