"""zotero_csljson FileImporter: Zotero "CSL JSON" exports (a JSON array of CSL items). Pure, no network.

Zotero ids such as 'http://zotero.org/users/123/items/ABCD2345' give the library and item key; other ids (e.g.
citation keys) are kept as the record's source_id without a Zotero identifier. Zotero puts its Extra field in
CSL 'note'; identifier lines there (PMID:, PMCID:, arXiv:, DOI:) are used. Items without a title are skipped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from researchos.contracts.datasources import BibRecord, DataSourceSpec
from researchos.contracts.importers import FileImportRequest

from .ids import clean, person_name, unique, year_of
from .model import ZoteroItem, collect_ids, to_record

_ZOTERO_URI = re.compile(r"(?:https?://zotero\.org/)?((?:users|groups)/(?:local/)?\w+)/items/([A-Z0-9]{8})$")
MAX_ITEMS = 100_000


class CslJsonError(ValueError):
    """Malformed CSL-JSON input."""


def _name(a: Mapping[str, Any]) -> str | None:
    if a.get("literal"):
        return clean(a["literal"])
    return person_name(a.get("given"), a.get("family"))


def _year(issued: object) -> int | None:
    if not isinstance(issued, Mapping):
        return None
    parts = issued.get("date-parts")
    if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
        first = parts[0][0]
        if isinstance(first, int) and not isinstance(first, bool):
            return first if 1500 < first < 2200 else None
        if isinstance(first, str) and first.strip().isdigit():
            return year_of(first)
        return None
    return year_of(clean(issued.get("raw") or issued.get("literal")))


def _tags(item: Mapping[str, Any]) -> tuple[str, ...]:
    raw = item.get("keyword")
    if not isinstance(raw, str):
        return ()
    return unique(t for t in (clean(x) for x in re.split(r"[,;]", raw)) if t)


def item_from_csl(item: Mapping[str, Any], index: int) -> ZoteroItem:
    ident = clean(item.get("id"))
    m = _ZOTERO_URI.search(ident or "")
    raw_authors = item.get("author")
    authors: list[Any] = raw_authors if isinstance(raw_authors, list) else []
    url = clean(item.get("URL"))
    ids = collect_ids(
        doi=clean(item.get("DOI")),
        extra=item.get("note") if isinstance(item.get("note"), str) else None,
        pmid=clean(item.get("PMID")),
        pmcid=clean(item.get("PMCID")),
        isbn=clean(item.get("ISBN")),
        url=url,
    )
    return ZoteroItem(
        source_ref=ident or f"csl-item-{index}",
        item_type=clean(item.get("type")),
        title=clean(item.get("title")),
        library=m[1] if m else None,
        key=m[2] if m else None,
        authors=unique(n for n in (_name(a) for a in authors if isinstance(a, Mapping)) if n),
        container=clean(item.get("container-title")),
        year=_year(item.get("issued")),
        abstract=clean(item.get("abstract")),
        url=url,
        tags=_tags(item),
        **ids,
    )


def parse_items(data: bytes) -> list[ZoteroItem]:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise CslJsonError(f"not valid UTF-8 JSON: {e}") from e
    if not isinstance(doc, list):
        raise CslJsonError("CSL-JSON export must be a JSON array of items")
    if len(doc) > MAX_ITEMS:
        raise CslJsonError(f"more than {MAX_ITEMS} items")
    bad = [i for i, x in enumerate(doc) if not isinstance(x, dict)]
    if bad:
        raise CslJsonError(f"items {bad[:5]} are not JSON objects")
    return [item_from_csl(x, i) for i, x in enumerate(doc)]


class ZoteroCslJsonImporter:
    spec = DataSourceSpec("zotero_csljson", "0.1.0", "bibliographic", "Zotero CSL-JSON export parser (pure)")

    def parse_file(self, req: FileImportRequest) -> list[BibRecord]:
        return [to_record(i, self.spec.name) for i in parse_items(req.data) if i.title]
