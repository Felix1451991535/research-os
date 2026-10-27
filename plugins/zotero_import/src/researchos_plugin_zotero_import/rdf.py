"""zotero_rdf FileImporter: Zotero RDF exports (rdf:RDF with z:itemType items, bib:Memo notes, z:Attachment and
z:Collection nodes). Pure, no network.

Safe XML: any DOCTYPE/ENTITY declaration is refused before parsing (no DTDs, so no entity expansion and no external
entity resolution); defusedxml is used when installed, else the stdlib parser. Zotero RDF does not carry Zotero
item keys, so records use the rdf:about reference as source_id and have no Zotero identifier.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # noqa: S405 - input is pre-screened: DOCTYPE/ENTITY refused
from collections.abc import Iterator
from typing import Any

from researchos.contracts.datasources import BibRecord, DataSourceSpec
from researchos.contracts.importers import FileImportRequest

from .ids import clean, html_text, license_of, person_name, unique, year_of
from .model import NOT_WORKS, AttachmentMeta, ZoteroItem, collect_ids, to_record

try:
    import defusedxml.ElementTree as _defused_et

    _defused: Any = _defused_et.fromstring
except ImportError:  # pragma: no cover - depends on the environment
    _defused = None


def _fromstring(data: bytes) -> ET.Element:
    if _defused is not None:
        root: ET.Element = _defused(data, forbid_dtd=True)
        return root
    return ET.fromstring(data)  # noqa: S314 - DTD/ENTITY declarations are refused before parsing


NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "z": "http://www.zotero.org/namespaces/export#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "bib": "http://purl.org/net/biblio#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "link": "http://purl.org/rss/1.0/modules/link/",
}
ABOUT, RESOURCE = f"{{{NS['rdf']}}}about", f"{{{NS['rdf']}}}resource"
_DTD = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)
MAX_BYTES = 50_000_000


class RdfError(ValueError):
    """Malformed or unsafe RDF/XML input."""


def safe_parse(data: bytes) -> ET.Element:
    if len(data) > MAX_BYTES:
        raise RdfError("RDF export too large")
    if _DTD.search(data):
        raise RdfError("DOCTYPE/ENTITY declarations are not allowed (unsafe XML)")
    try:
        root = _fromstring(data)
    except ET.ParseError as e:
        raise RdfError(f"malformed XML: {e}") from e
    if root.tag != f"{{{NS['rdf']}}}RDF":
        raise RdfError("not a Zotero RDF export (root element is not rdf:RDF)")
    return root


def _text(el: ET.Element | None) -> str | None:
    return clean("".join(el.itertext())) if el is not None else None


def _find_text(el: ET.Element, path: str) -> str | None:
    return _text(el.find(path, NS))


class _Doc:
    def __init__(self, root: ET.Element) -> None:
        self.nodes = list(root)
        self.by_about = {n.get(ABOUT): n for n in self.nodes if n.get(ABOUT)}
        self.parent: dict[str, str] = {}
        self.members: dict[str, list[str]] = {}
        for c in self.nodes:
            if c.tag == f"{{{NS['z']}}}Collection" and c.get(ABOUT):
                for part in c.findall("dcterms:hasPart", NS):
                    target = part.get(RESOURCE) or ""
                    if target in self.by_about and self.by_about[target].tag == c.tag:
                        self.parent[target] = c.get(ABOUT) or ""
                    else:
                        self.members.setdefault(target, []).append(c.get(ABOUT) or "")

    def collection_path(self, about: str) -> str | None:
        names, seen, cur = [], set(), about
        while cur and cur not in seen:
            seen.add(cur)
            name = _find_text(self.by_about[cur], "dc:title") if cur in self.by_about else None
            names.append(name or cur)
            cur = self.parent.get(cur, "")
        return "/".join(reversed(names)) or None

    def resolve(self, el: ET.Element) -> ET.Element | None:
        ref = el.get(RESOURCE)
        return self.by_about.get(ref) if ref else next(iter(el), None)


def _authors(item: ET.Element) -> tuple[str, ...]:
    out = []
    for p in item.iterfind("bib:authors/rdf:Seq/rdf:li/foaf:Person", NS):
        given = _find_text(p, "foaf:givenName") or _find_text(p, "foaf:givenname")
        name = person_name(given, _find_text(p, "foaf:surname"))
        if name:
            out.append(name)
    return unique(out)


def _identifiers(item: ET.Element) -> tuple[dict[str, str], str | None]:
    """('DOI'/'ISBN' -> value, URL) from dc:identifier elements."""
    typed: dict[str, str] = {}
    url = None
    for ident in item.findall("dc:identifier", NS):
        uri = ident.find("dcterms:URI/rdf:value", NS)
        if uri is not None:
            url = url or _text(uri)
            continue
        kind, _, value = (_text(ident) or "").partition(" ")
        if kind.upper() in ("DOI", "ISBN") and value:
            typed.setdefault(kind.upper(), value)
    return typed, url


def _tags(item: ET.Element) -> tuple[str, ...]:
    tags = []
    for s in item.findall("dc:subject", NS):
        tags.append(_find_text(s, "z:AutomaticTag/rdf:value") if len(s) else _text(s))
    return unique(t for t in tags if t)


def _linked(doc: _Doc, item: ET.Element, path: str) -> Iterator[ET.Element]:
    for el in item.findall(path, NS):
        target = doc.by_about.get(el.get(RESOURCE) or "")
        if target is not None:
            yield target


def _attachment(node: ET.Element) -> AttachmentMeta:
    url = _find_text(node, "dc:identifier/dcterms:URI/rdf:value")
    return AttachmentMeta(_find_text(node, "dc:title"), _find_text(node, "link:type"), url)


def _item(doc: _Doc, node: ET.Element, index: int) -> ZoteroItem:
    about = node.get(ABOUT) or f"rdf-item-{index}"
    typed, url = _identifiers(node)
    part = node.find("dcterms:isPartOf", NS)
    container = doc.resolve(part) if part is not None else None
    ids = collect_ids(doi=typed.get("DOI"), isbn=typed.get("ISBN"), extra=_find_text(node, "dc:description"), url=url)
    return ZoteroItem(
        source_ref=about,
        item_type=_find_text(node, "z:itemType"),
        title=_find_text(node, "dc:title"),
        authors=_authors(node),
        container=_find_text(container, "dc:title") if container is not None else None,
        year=year_of(_find_text(node, "dc:date")),
        abstract=_find_text(node, "dcterms:abstract"),
        url=url,
        tags=_tags(node),
        collections=unique(p for p in (doc.collection_path(c) for c in doc.members.get(about, [])) if p),
        notes=unique(
            n
            for n in (html_text(_find_text(m, "rdf:value")) for m in _linked(doc, node, "dcterms:isReferencedBy"))
            if n
        ),
        attachments=tuple(_attachment(a) for a in _linked(doc, node, "link:link")),
        license=license_of(_find_text(node, "dc:rights")),
        **ids,
    )


def parse_items(data: bytes) -> list[ZoteroItem]:
    doc = _Doc(safe_parse(data))
    out = []
    for i, node in enumerate(doc.nodes):
        item_type = _find_text(node, "z:itemType")
        if item_type and item_type not in NOT_WORKS:
            out.append(_item(doc, node, i))
    return out


class ZoteroRdfImporter:
    spec = DataSourceSpec("zotero_rdf", "0.1.0", "bibliographic", "Zotero RDF export parser (pure, safe XML)")

    def parse_file(self, req: FileImportRequest) -> list[BibRecord]:
        return [to_record(i, self.spec.name) for i in parse_items(req.data) if i.title]
