"""OpenAlex citation chaining and retraction status (core 1.4 optional capabilities of lit_openalex).

- references(record): works cited BY the record  -> /works?filter=cited_by:W..
- cited_by(record):   works citing the record    -> /works?filter=cites:W..
- retraction_status:  /works?filter=doi:a|b&select=doi,is_retracted  (absent from OpenAlex => unknown, never False)
A record without an OpenAlex id is resolved once by DOI (/works/doi:<doi>); no id and no DOI -> empty page + warning.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from researchos.contracts.datasources import BibRecord, IdScheme, RetractionStatus, SearchPage
from researchos.contracts.http import HttpContext

from .json_sources import OpenAlexSource as _SearchOnly
from .json_sources import openalex
from .normalize import collect, doi

_WID = re.compile(r"(W\d+)$")


def _work_id(record: BibRecord, http: HttpContext, api: str) -> str | None:
    for scheme, value in record.identifiers:
        if scheme == "openalex" and (m := _WID.search(value)):
            return m[1]
    if record.source == "lit_openalex" and (m := _WID.search(record.source_id)):
        return m[1]
    d = next((v for s, v in record.identifiers if s == "doi"), None)
    if not d:
        return None
    r = http.get(f"{api}/doi:{d}")
    if r.status != 200:
        return None
    m = _WID.search(str(json.loads(r.text()).get("id") or ""))
    return m[1] if m else None


class OpenAlexSource(_SearchOnly):
    """lit_openalex: search + CitationGraphSource + RetractionSource."""

    def _chain(self, flt: str, record: BibRecord, http: HttpContext, cursor: str | None, page_size: int) -> SearchPage:
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be 1..100")
        wid = _work_id(record, http, self.api)
        if wid is None:
            return SearchPage((), warnings=("OpenAlex: record has no OpenAlex id or resolvable DOI",))
        r = http.get(self.api, params={"filter": f"{flt}:{wid}", "per-page": str(page_size), "cursor": cursor or "*"})
        if r.status != 200:
            return SearchPage((), warnings=(f"OpenAlex HTTP {r.status}",))
        data = json.loads(r.text())
        records, warnings = collect(data.get("results", [])[:page_size], openalex, "OpenAlex")
        meta = data.get("meta", {})
        return SearchPage(records, meta.get("next_cursor") if records else None, meta.get("count"), warnings)

    def references(
        self,
        record: BibRecord,
        http: HttpContext,
        settings: Mapping[str, Any],
        cursor: str | None = None,
        page_size: int = 25,
    ) -> SearchPage:
        return self._chain("cited_by", record, http, cursor, page_size)

    def cited_by(
        self,
        record: BibRecord,
        http: HttpContext,
        settings: Mapping[str, Any],
        cursor: str | None = None,
        page_size: int = 25,
    ) -> SearchPage:
        return self._chain("cites", record, http, cursor, page_size)

    def retraction_status(
        self, identifiers: tuple[tuple[IdScheme, str], ...], http: HttpContext, settings: Mapping[str, Any]
    ) -> tuple[RetractionStatus, ...]:
        if len(identifiers) > 50:
            raise ValueError("at most 50 identifiers per call")
        dois = {d: (s, v) for s, v in identifiers if s == "doi" and (d := doi(v))}
        known: dict[str, bool] = {}
        if dois:
            r = http.get(
                self.api,
                params={"filter": "doi:" + "|".join(sorted(dois)), "select": "doi,is_retracted", "per-page": "50"},
            )
            if r.status != 200:
                raise ValueError(f"OpenAlex HTTP {r.status}")
            for item in json.loads(r.text()).get("results", []):
                d = doi(item.get("doi"))
                if d and isinstance(item.get("is_retracted"), bool):
                    known[d] = item["is_retracted"]
        out = []
        for scheme, value in identifiers:
            d = doi(value) if scheme == "doi" else None
            out.append(
                RetractionStatus(
                    (scheme, value),
                    known.get(d) if d else None,
                    "lit_openalex",
                    f"https://doi.org/{d}" if d and known.get(d) else None,
                )
            )
        return tuple(out)
