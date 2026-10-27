"""Bibliographic source: Crossref-style JSON search, normalized to BibRecord (unknown fields stay None)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from researchos.contracts.datasources import BibRecord, DataSourceSpec, SearchPage, SearchQuery
from researchos.contracts.http import HttpContext

API = "https://api.crossref.org/works"


def _year(item: dict[str, Any]) -> int | None:
    parts = (item.get("issued") or {}).get("date-parts") or [[None]]
    y = parts[0][0] if parts and parts[0] else None
    return int(y) if isinstance(y, int) else None


def _authors(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        " ".join(x for x in (a.get("given"), a.get("family")) if x) for a in item.get("author", []) if a.get("family")
    )


def _license(item: dict[str, Any]) -> tuple[str | None, str | None]:
    for lic in item.get("license", []):
        url = str(lic.get("URL", ""))
        if "creativecommons.org/licenses/by/4.0" in url:
            return "CC-BY-4.0", url
    return None, None


def to_record(item: dict[str, Any]) -> BibRecord:
    doi = str(item["DOI"]).lower()
    lic, lic_src = _license(item)
    pdf = next((ln.get("URL") for ln in item.get("link", []) if ln.get("content-type") == "application/pdf"), None)
    return BibRecord(
        source="hello_crossref",
        source_id=doi,
        title=(item.get("title") or [""])[0] or doi,
        authors=_authors(item),
        year=_year(item),
        venue=(item.get("container-title") or [None])[0],
        identifiers=(("doi", doi),),
        url=f"https://doi.org/{doi}",
        oa_pdf_url=pdf,
        license=lic,
        license_source=lic_src,
        kind="review" if item.get("type") == "review-article" else "article",
    )


class CrossrefLikeSource:
    spec = DataSourceSpec(
        "hello_crossref",
        "0.1.0",
        "bibliographic",
        "Crossref works search (reference plugin)",
        hosts=("api.crossref.org",),
    )

    def search(self, query: SearchQuery, http: HttpContext, settings: Mapping[str, Any]) -> SearchPage:
        params = {"query": query.text, "rows": str(query.page_size)}
        if query.cursor:
            params["offset"] = query.cursor
        r = http.get(API, params=params)
        if r.status != 200:
            return SearchPage((), warnings=(f"crossref HTTP {r.status}",))
        msg = json.loads(r.text())["message"]
        items = msg.get("items", [])
        nxt = str(int(query.cursor or 0) + len(items)) if len(items) == query.page_size else None
        return SearchPage(tuple(to_record(i) for i in items if i.get("DOI")), nxt, msg.get("total-results"))
