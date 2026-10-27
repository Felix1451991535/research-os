"""Parsed Zotero item (all fields the plugin understands) and its mapping to the contract BibRecord.

BibRecord (contracts 1.2) has no fields for tags, collections, notes or attachment metadata. They are parsed and
kept on ZoteroItem (tested) but cannot be handed to the KnowledgeWriter yet; see PROPOSED_CONTRACT_CHANGES.md.
The Zotero identity is carried as ("other", "zotero:<library>/<itemKey>") because IdScheme has no "zotero".
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TypedDict

from researchos.contracts.datasources import BibRecord, IdScheme

from .ids import extra_ids, norm_arxiv, norm_doi, norm_isbns, norm_pmcid, norm_pmid, unique

ZOTERO_ID_PREFIX = "zotero:"
LIBRARY = re.compile(r"^(?:users|groups)/\d+$")
ITEM_KEY = re.compile(r"^[A-Z0-9]{8}$")

KINDS = {
    "journalArticle": "article",
    "preprint": "preprint",
    "thesis": "thesis",
    "patent": "patent",
    "book": "book",
    "bookSection": "chapter",
    "conferencePaper": "conference-paper",
    "report": "report",
    "dataset": "dataset",
    "magazineArticle": "magazine-article",
    "newspaperArticle": "newspaper-article",
    "webpage": "webpage",
    # CSL-JSON types (zotero_csljson)
    "article-journal": "article",
    "chapter": "chapter",
    "paper-conference": "conference-paper",
    "article-magazine": "magazine-article",
    "article-newspaper": "newspaper-article",
}
NOT_WORKS = {
    "note": "standalone note (not a bibliographic work)",
    "attachment": "attachment item (metadata only; files are never downloaded)",
    "annotation": "annotation item (not a bibliographic work)",
}


@dataclass(frozen=True)
class AttachmentMeta:
    title: str | None
    content_type: str | None
    url: str | None


@dataclass(frozen=True)
class ZoteroItem:
    source_ref: str  # stable reference within the export / library
    item_type: str | None
    title: str | None
    library: str | None = None  # users/<id> | groups/<id> when known
    key: str | None = None  # 8-character Zotero item key when known
    version: int | None = None
    authors: tuple[str, ...] = ()
    container: str | None = None
    year: int | None = None
    abstract: str | None = None
    url: str | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    arxiv: str | None = None
    isbns: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()  # collection paths ("Parent/Child") or keys
    notes: tuple[str, ...] = ()
    attachments: tuple[AttachmentMeta, ...] = ()
    license: str | None = None

    @property
    def zotero_ref(self) -> str | None:
        if self.library and self.key and LIBRARY.match(self.library) and ITEM_KEY.match(self.key):
            return f"{self.library}/{self.key}"
        return None

    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, default=str).encode()).hexdigest()


class Ids(TypedDict):
    doi: str | None
    pmid: str | None
    pmcid: str | None
    arxiv: str | None
    isbns: tuple[str, ...]


def collect_ids(
    *,
    doi: str | None,
    extra: str | None,
    pmid: str | None = None,
    pmcid: str | None = None,
    arxiv: str | None = None,
    isbn: str | None = None,
    url: str | None = None,
) -> Ids:
    """Normalised identifiers from explicit fields first, then Zotero's Extra lines, then an arXiv URL."""
    ex = extra_ids(extra)

    def first(fn: Callable[[str | None], str | None], *cands: str | None) -> str | None:
        return next((v for v in (fn(c) for c in cands) if v), None)

    return {
        "doi": first(norm_doi, doi, *ex.get("doi", [])),
        "pmid": first(norm_pmid, pmid, *ex.get("pmid", [])),
        "pmcid": first(norm_pmcid, pmcid, *ex.get("pmcid", [])),
        "arxiv": first(norm_arxiv, arxiv, *ex.get("arxiv", []), url if url and "arxiv.org" in url else None),
        "isbns": unique(i for v in (isbn, *ex.get("isbn", [])) for i in norm_isbns(v)),
    }


def identifiers(item: ZoteroItem) -> tuple[tuple[IdScheme, str], ...]:
    ids: list[tuple[IdScheme, str]] = []
    if item.zotero_ref:
        ids.append(("zotero", item.zotero_ref))  # core 1.3: library-scoped hard identity ("users/<id>/<key>")
    for scheme, value in (("doi", item.doi), ("pmid", item.pmid), ("pmcid", item.pmcid), ("arxiv", item.arxiv)):
        if value:
            ids.append((scheme, value))  # type: ignore[arg-type]
    ids.extend(("isbn", v) for v in item.isbns)
    return tuple(ids)


def to_record(item: ZoteroItem, source: str) -> BibRecord:
    if not item.title:
        raise ValueError(f"{item.source_ref}: item has no title")
    return BibRecord(
        source=source,
        source_id=item.zotero_ref or item.source_ref,
        title=item.title,
        authors=item.authors,
        year=item.year,
        venue=item.container,
        abstract=item.abstract,
        identifiers=identifiers(item),
        url=item.url,
        oa_pdf_url=None,
        license=item.license,
        license_source="Zotero Rights field" if item.license else None,
        kind=KINDS.get(item.item_type or "", item.item_type or "article"),
    )


def zotero_ref_of(record: BibRecord) -> str | None:
    """'<library>/<itemKey>' from a record's ('zotero', ...) identifier (legacy: ('other', 'zotero:...')) or a
    zotero source_id."""
    for scheme, value in record.identifiers:
        if scheme == "zotero":
            return value
        if scheme == "other" and value.startswith(ZOTERO_ID_PREFIX):
            return value.removeprefix(ZOTERO_ID_PREFIX)
    lib, _, key = record.source_id.rpartition("/")
    return record.source_id if LIBRARY.match(lib) and ITEM_KEY.match(key) else None
