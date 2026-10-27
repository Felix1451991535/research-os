"""Crossref, OpenAlex and optional Semantic Scholar via host HTTP only."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from researchos.contracts.datasources import BibRecord, DataSourceSpec, SearchPage, SearchQuery
from researchos.contracts.http import HttpContext

from . import filters
from .normalize import collect, doi, open_license, text, year


def crossref(item: dict[str, Any]) -> BibRecord:
    ident = doi(item.get("DOI"))
    title = text((item.get("title") or [None])[0])
    if not ident or not title:
        raise ValueError("Crossref record lacks DOI or title")
    parts = (item.get("issued") or {}).get("date-parts") or [[]]
    license_url = next((x.get("URL") for x in item.get("license", []) if open_license(x.get("URL"))), None)
    pdf = next((x.get("URL") for x in item.get("link", []) if x.get("content-type") == "application/pdf"), None)
    authors = tuple(
        " ".join(filter(None, (text(a.get("given")), text(a.get("family"))))) for a in item.get("author", [])
    )
    return BibRecord(
        "lit_crossref",
        ident,
        title,
        tuple(a for a in authors if a),
        year(parts[0][0]) if parts[0] else None,
        text((item.get("container-title") or [None])[0]),
        text(item.get("abstract")),
        (("doi", ident),),
        f"https://doi.org/{ident}",
        text(pdf),
        open_license(license_url),
        text(license_url),
        "review" if item.get("type") == "review-article" else str(item.get("type") or "article"),
    )


def abstract(index: Any) -> str | None:
    if not index:
        return None
    positions: dict[int, str] = {}
    for word, offsets in index.items():
        for pos in offsets:
            if not isinstance(pos, int) or pos < 0 or pos > 100000 or pos in positions:
                raise ValueError("Invalid inverted abstract")
            positions[pos] = word
    if set(positions) != set(range(len(positions))):
        raise ValueError("Incomplete inverted abstract")
    return " ".join(positions[i] for i in range(len(positions))) or None


def openalex(item: dict[str, Any]) -> BibRecord:
    ident, title = text(item.get("id")), text(item.get("title"))
    if not ident or not title:
        raise ValueError("OpenAlex record lacks id or title")
    d = doi(item.get("doi"))
    loc = item.get("best_oa_location") or {}
    source = (item.get("primary_location") or {}).get("source") or {}
    lic = open_license(loc.get("license"))
    return BibRecord(
        "lit_openalex",
        ident,
        title,
        tuple(
            a["author"]["display_name"]
            for a in item.get("authorships", [])
            if (a.get("author") or {}).get("display_name")
        ),
        year(item.get("publication_year")),
        text(source.get("display_name")),
        abstract(item.get("abstract_inverted_index")),
        (("openalex", ident),) + ((("doi", d),) if d else ()),
        ident,
        text(loc.get("pdf_url")),
        lic,
        ident if lic else None,
        str(item.get("type") or "article"),
    )


def semantic(item: dict[str, Any]) -> BibRecord:
    ident, title = text(item.get("paperId")), text(item.get("title"))
    if not ident or not title:
        raise ValueError("Semantic Scholar record lacks id or title")
    d = doi((item.get("externalIds") or {}).get("DOI"))
    return BibRecord(
        "lit_semantic",
        ident,
        title,
        tuple(a["name"] for a in item.get("authors", []) if a.get("name")),
        year(item.get("year")),
        text(item.get("venue")),
        text(item.get("abstract")),
        (("s2", ident),) + ((("doi", d),) if d else ()),
        text(item.get("url")),
        text((item.get("openAccessPdf") or {}).get("url")),
    )


class CrossrefSource:
    spec = DataSourceSpec("lit_crossref", "0.1.0", "bibliographic", "Crossref works", ("api.crossref.org",))
    api = "https://api.crossref.org/works"

    def search(self, query: SearchQuery, http: HttpContext, settings: Mapping[str, Any]) -> SearchPage:
        validate(query)
        params = {"query": query.text, "rows": str(query.page_size), "cursor": query.cursor or "*"}
        if who := filters.author(query):
            params.pop("query")
            params["query.author"] = who
        if fy := filters.from_year(query):
            params["filter"] = f"from-pub-date:{fy}"
        if settings.get("polite_email"):
            params["mailto"] = str(settings["polite_email"])
        response = http.get(self.api, params=params)
        if response.status != 200:
            return SearchPage((), warnings=(f"Crossref HTTP {response.status}",))
        msg = json.loads(response.text())["message"]
        records, warnings = collect(msg.get("items", [])[: query.page_size], crossref, "Crossref")
        return SearchPage(
            records, msg.get("next-cursor") if msg.get("items") else None, msg.get("total-results"), warnings
        )


def validate(query: SearchQuery) -> None:
    if not 1 <= query.page_size <= 100 or not query.text.strip():
        raise ValueError("Query must be nonempty; page_size must be 1..100")
    filters.check(query)


class OpenAlexSource:
    spec = DataSourceSpec("lit_openalex", "0.1.0", "bibliographic", "OpenAlex works", ("api.openalex.org",))
    api = "https://api.openalex.org/works"

    def search(self, query: SearchQuery, http: HttpContext, settings: Mapping[str, Any]) -> SearchPage:
        validate(query)
        params = {"search": query.text, "per-page": str(query.page_size), "cursor": query.cursor or "*"}
        flt = []
        if who := filters.author(query):
            params.pop("search")
            flt.append(f"raw_author_name.search:{who.replace(',', ' ')}")
        if fy := filters.from_year(query):
            flt.append(f"from_publication_date:{fy}-01-01")
        if flt:
            params["filter"] = ",".join(flt)
        response = http.get(self.api, params=params)
        if response.status != 200:
            return SearchPage((), warnings=(f"OpenAlex HTTP {response.status}",))
        data = json.loads(response.text())
        records, warnings = collect(data.get("results", [])[: query.page_size], openalex, "OpenAlex")
        meta = data.get("meta", {})
        return SearchPage(records, meta.get("next_cursor"), meta.get("count"), warnings)


class SemanticScholarSource:
    spec = DataSourceSpec(
        "lit_semantic", "0.1.0", "bibliographic", "Optional keyed Semantic Scholar search", ("api.semanticscholar.org",)
    )
    api = "https://api.semanticscholar.org/graph/v1/paper/search"

    def search(self, query: SearchQuery, http: HttpContext, settings: Mapping[str, Any]) -> SearchPage:
        validate(query)
        if filters.author(query):
            return SearchPage((), warnings=("Semantic Scholar: author filter not supported by paper search",))
        key = settings.get("semantic_scholar_api_key")
        if not key:
            return SearchPage((), warnings=("Semantic Scholar disabled: API key not configured",))
        offset = int(query.cursor or 0)
        if offset < 0:
            raise ValueError("Negative offset")
        response = http.get(
            self.api,
            params={
                "query": query.text,
                "limit": str(query.page_size),
                "offset": str(offset),
                "fields": "title,authors,year,venue,abstract,externalIds,url,openAccessPdf",
                **({"year": f"{fy}-"} if (fy := filters.from_year(query)) else {}),
            },
            headers={"x-api-key": str(key)},
        )
        if response.status != 200:
            return SearchPage((), warnings=(f"Semantic Scholar HTTP {response.status}",))
        data = json.loads(response.text())
        records, warnings = collect(data.get("data", [])[: query.page_size], semantic, "Semantic Scholar")
        nxt = str(data["next"]) if data.get("next") is not None else None
        return SearchPage(records, nxt, data.get("total"), warnings)
