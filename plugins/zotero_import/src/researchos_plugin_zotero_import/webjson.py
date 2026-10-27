"""Zotero Web API v3 item JSON (format=json) -> ZoteroItem."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .ids import clean, html_text, license_of, person_name, unique, year_of
from .model import AttachmentMeta, ZoteroItem, collect_ids

CONTAINER_FIELDS = (
    "publicationTitle",
    "bookTitle",
    "proceedingsTitle",
    "websiteTitle",
    "blogTitle",
    "forumTitle",
    "encyclopediaTitle",
    "dictionaryTitle",
    "repository",
)


def _creator(c: Mapping[str, Any]) -> str | None:
    if c.get("name"):
        return clean(c["name"])
    return person_name(c.get("firstName"), c.get("lastName"))


def _library(obj: Mapping[str, Any]) -> str | None:
    lib = obj.get("library")
    if not isinstance(lib, Mapping) or lib.get("type") not in ("user", "group") or lib.get("id") is None:
        return None
    return f"{'users' if lib['type'] == 'user' else 'groups'}/{lib['id']}"


def _version(obj: Mapping[str, Any], data: Mapping[str, Any]) -> int | None:
    v = data.get("version", obj.get("version"))
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _attachment(data: Mapping[str, Any]) -> tuple[AttachmentMeta, ...]:
    if data.get("itemType") != "attachment":
        return ()
    return (AttachmentMeta(clean(data.get("title")), clean(data.get("contentType")), clean(data.get("url"))),)


def item_from_api(obj: Mapping[str, Any]) -> ZoteroItem:
    data = obj.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("Zotero API item without a 'data' object")
    raw_meta = obj.get("meta")
    meta: Mapping[str, Any] = raw_meta if isinstance(raw_meta, Mapping) else {}
    library, key = _library(obj), clean(data.get("key") or obj.get("key"))
    creators = [c for c in data.get("creators") or [] if isinstance(c, Mapping)]
    authors = unique(n for n in (_creator(c) for c in creators if c.get("creatorType") == "author") if n)
    tags = unique(t for t in (clean(x.get("tag")) for x in data.get("tags") or [] if isinstance(x, Mapping)) if t)
    ids = collect_ids(
        doi=data.get("DOI"),
        extra=data.get("extra"),
        isbn=data.get("ISBN"),
        arxiv=data.get("archiveID"),
        url=data.get("url"),
    )
    return ZoteroItem(
        source_ref=f"{library}/{key}",
        item_type=clean(data.get("itemType")),
        title=clean(data.get("title")),
        library=library,
        key=key,
        version=_version(obj, data),
        authors=authors,
        container=next((v for v in (clean(data.get(f)) for f in CONTAINER_FIELDS) if v), None),
        year=year_of(clean(meta.get("parsedDate"))) or year_of(clean(data.get("date"))),
        abstract=clean(data.get("abstractNote")),
        url=clean(data.get("url")),
        tags=tags,
        collections=tuple(str(c) for c in data.get("collections") or ()),
        notes=tuple(n for n in (html_text(data.get("note")),) if n and data.get("itemType") == "note"),
        attachments=_attachment(data),
        license=license_of(clean(data.get("rights"))),
        **ids,
    )
