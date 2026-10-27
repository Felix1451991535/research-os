"""Citation chaining, retraction status (OpenAlex) and Unpaywall OA locations. SYNTHETIC fixtures (CC0), offline."""

from __future__ import annotations

import json

import pytest
from researchos.contracts.datasources import BibRecord
from researchos.sdk.testing import FakeHttp, json_response

from researchos_plugin_litsearch.openalex_graph import OpenAlexSource
from researchos_plugin_litsearch.unpaywall import UnpaywallResolver

API = "https://api.openalex.org/works"
WORK = {
    "id": "https://openalex.org/W200",
    "title": "Synthetic citing paper",
    "publication_year": 2024,
    "doi": "https://doi.org/10.5555/syn.2",
    "authorships": [],
    "type": "article",
}


def page(*items: dict) -> str:
    return json.dumps({"results": list(items), "meta": {"count": len(items), "next_cursor": None}})


def test_references_and_cited_by_use_openalex_filters() -> None:
    fake = FakeHttp(
        {
            f"{API}?cursor=%2A&filter=cited_by%3AW100": json_response(API, page(WORK)),
            f"{API}?cursor=%2A&filter=cites%3AW100": json_response(API, page()),
        },
        ("api.openalex.org",),
    )
    rec = BibRecord("lit_openalex", "https://openalex.org/W100", "Seed", identifiers=(("openalex", "W100"),))
    refs = OpenAlexSource().references(rec, fake, {})
    assert [r.title for r in refs.records] == ["Synthetic citing paper"]
    assert not OpenAlexSource().cited_by(rec, fake, {}).records


def test_chaining_resolves_doi_once_and_reports_unknown_records() -> None:
    fake = FakeHttp(
        {
            f"{API}/doi:10.5555/syn.1": json_response(API, json.dumps({"id": "https://openalex.org/W100"})),
            f"{API}?cursor=%2A&filter=cites%3AW100": json_response(API, page(WORK)),
        },
        ("api.openalex.org",),
    )
    rec = BibRecord("lit_crossref", "10.5555/syn.1", "Seed", identifiers=(("doi", "10.5555/syn.1"),))
    assert len(OpenAlexSource().cited_by(rec, fake, {}).records) == 1
    unknown = OpenAlexSource().cited_by(BibRecord("x", "1", "No ids"), fake, {})
    assert not unknown.records and unknown.warnings


def test_retraction_status_unknown_is_never_false() -> None:
    body = json.dumps(
        {
            "results": [
                {"doi": "https://doi.org/10.5555/retracted", "is_retracted": True},
                {"doi": "https://doi.org/10.5555/fine", "is_retracted": False},
            ]
        }
    )
    fake = FakeHttp({API: json_response(API, body)}, ("api.openalex.org",))
    ids = (("doi", "10.5555/RETRACTED"), ("doi", "10.5555/fine"), ("doi", "10.5555/absent"), ("pmid", "1"))
    st = OpenAlexSource().retraction_status(ids, fake, {})  # type: ignore[arg-type]
    assert [s.retracted for s in st] == [True, False, None, None]
    assert st[0].notice_url == "https://doi.org/10.5555/retracted"
    assert "filter=doi%3A10.5555%2Fabsent%7C10.5555%2Ffine%7C10.5555%2Fretracted" in fake.calls[0]


def test_unpaywall_license_only_when_explicit() -> None:
    url = "https://api.unpaywall.org/v2/10.5555/syn.1"
    body = {
        "best_oa_location": {
            "url_for_pdf": "https://repo.example.org/syn.pdf",
            "license": "cc-by",
            "version": "publishedVersion",
            "host_type": "repository",
        }
    }
    fake = FakeHttp({url: json_response(url, json.dumps(body))}, ("api.unpaywall.org",))
    loc = UnpaywallResolver().resolve("https://doi.org/10.5555/SYN.1", fake, {"polite_email": "owner@example.org"})
    assert loc is not None and loc.license == "CC-BY" and loc.pdf_url.endswith("syn.pdf")
    body["best_oa_location"]["license"] = None  # type: ignore[index]
    fake2 = FakeHttp({url: json_response(url, json.dumps(body))}, ("api.unpaywall.org",))
    loc2 = UnpaywallResolver().resolve("10.5555/syn.1", fake2, {"polite_email": "owner@example.org"})
    assert loc2 is not None and loc2.license is None and loc2.license_source is None
    with pytest.raises(ValueError):
        UnpaywallResolver().resolve("10.5555/syn.1", fake, {})
    nf = FakeHttp({url: json_response(url, "{}", 404)}, ("api.unpaywall.org",))
    assert UnpaywallResolver().resolve("10.5555/syn.1", nf, {"polite_email": "o@example.org"}) is None
