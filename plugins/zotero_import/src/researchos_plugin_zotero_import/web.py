"""zotero_web Importer: imports SELECTED Zotero items through the Zotero Web API v3 (https://api.zotero.org).

- Records carrying a Zotero reference (('other', 'zotero:<library>/<key>') or source_id '<library>/<key>') are
  re-fetched in batches of 50 keys (itemKey=...), following Link rel="next" / Total-Results pagination.
- Records parsed by zotero_csljson / zotero_rdf without a usable reference (or with refetch=false) are written
  exactly as parsed.
- Idempotency key: 'zotero:<library>/<key>@v<version>' (file records: source, id and content hash). Items repeated
  within one import are written once. Dedupe across works is the KnowledgeWriter's job (DOI > PMID > arXiv).
- Attachments are never downloaded. The local Zotero desktop API (loopback) is not used.
- The API key (settings.api_key) is only sent as the Zotero-API-Key header; it never appears in results,
  exceptions or logs (all messages are scrubbed). The plugin does not log.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from researchos.contracts.datasources import BibRecord, DataSourceSpec
from researchos.contracts.http import HttpContext, HttpDenied, HttpResponse, HttpUnavailable
from researchos.contracts.importers import ImportItemResult, KnowledgeWriter

from .model import ITEM_KEY, NOT_WORKS, ZoteroItem, to_record, zotero_ref_of
from .webjson import item_from_api

HOST = "api.zotero.org"
API = f"https://{HOST}"
BATCH = 50
MAX_PAGES = 200
FILE_SOURCES = ("zotero_csljson", "zotero_rdf")
_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')
_LOCAL_KEY = re.compile(r"/items/([A-Z0-9]{8})$")

SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "library_type": {"enum": ["user", "group"], "description": "library used for records without a library"},
        "library_id": {"type": "string", "pattern": "^[0-9]+$"},
        "api_key": {
            "type": "string",
            "minLength": 1,
            "writeOnly": True,
            "format": "password",
            "x-researchos-secret": True,
            "description": "Zotero API key with read access. Secret: sent only as a request header, "
            "never logged, returned or stored by the plugin.",
        },
        "refetch": {"type": "boolean", "description": "re-fetch items that carry a Zotero key (default true)"},
    },
    "dependentRequired": {"library_type": ["library_id"], "library_id": ["library_type"]},
    "additionalProperties": False,
}


class ZoteroApiError(RuntimeError):
    """Zotero API refused or returned an unusable response. Messages never contain the API key."""


def scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def _header(resp: HttpResponse, name: str) -> str | None:
    return next((v for k, v in resp.headers.items() if k.lower() == name.lower()), None)


def _page(resp: HttpResponse) -> list[dict[str, Any]]:
    if resp.status == 403:
        raise ZoteroApiError("access denied (HTTP 403): check the library id and the API key's read permission")
    if resp.status == 404:
        raise ZoteroApiError("library not found (HTTP 404)")
    if resp.status != 200:
        raise ZoteroApiError(f"Zotero API HTTP {resp.status}")
    try:
        body = json.loads(resp.body)
    except ValueError as e:
        raise ZoteroApiError("Zotero API returned invalid JSON") from e
    if not isinstance(body, list) or not all(isinstance(x, dict) for x in body):
        raise ZoteroApiError("Zotero API returned an unexpected response shape")
    return body


def next_start(resp: HttpResponse, current: int, got: int) -> int | None:
    """Start offset of the next page from Link rel="next" (preferred) or Total-Results; None when done."""
    m = _NEXT.search(_header(resp, "link") or "")
    if m:
        u = urlsplit(m[1])
        start = (parse_qs(u.query).get("start") or [""])[0]
        if u.scheme != "https" or u.hostname != HOST or not start.isdigit() or int(start) <= current:
            raise ZoteroApiError("invalid pagination link from the Zotero API")
        return int(start)
    total = (_header(resp, "total-results") or "").strip()
    if got and total.isdigit() and current + got < int(total):
        return current + got
    return None


def paginate(
    http: HttpContext, url: str, params: Mapping[str, str], headers: Mapping[str, str]
) -> list[dict[str, Any]]:
    start, items = 0, []
    for _ in range(MAX_PAGES):
        resp = http.get(url, params={**params, "start": str(start)}, headers=headers)
        page = _page(resp)
        items.extend(page)
        nxt = next_start(resp, start, len(page))
        if nxt is None:
            return items
        start = nxt
    raise ZoteroApiError(f"more than {MAX_PAGES} result pages")


def fetch_items(http: HttpContext, library: str, keys: list[str], secret: str | None) -> list[dict[str, Any]]:
    headers = {"Zotero-API-Version": "3", **({"Zotero-API-Key": secret} if secret else {})}
    out: list[dict[str, Any]] = []
    for i in range(0, len(keys), BATCH):
        params = {"itemKey": ",".join(keys[i : i + BATCH]), "format": "json", "limit": str(BATCH)}
        out.extend(paginate(http, f"{API}/{library}/items", params, headers))
    return out


def _settings_library(settings: Mapping[str, Any]) -> str | None:
    kind, lib_id = settings.get("library_type"), str(settings.get("library_id") or "")
    if kind in ("user", "group") and lib_id.isdigit():
        return f"{'users' if kind == 'user' else 'groups'}/{lib_id}"
    return None


def reference(record: BibRecord, settings: Mapping[str, Any]) -> str | None:
    """'<library>/<key>' to fetch, or None (written as parsed / rejected)."""
    if settings.get("refetch", True) is False:
        return None
    ref = zotero_ref_of(record)
    lib = _settings_library(settings)
    if ref is None and lib:
        bare = record.source_id if ITEM_KEY.match(record.source_id) else None
        m = _LOCAL_KEY.search(record.source_id)
        key = bare or (m[1] if m else None)
        ref = f"{lib}/{key}" if key else None
    return ref


def _file_key(record: BibRecord) -> str:
    digest = hashlib.sha256(json.dumps(asdict(record), sort_keys=True).encode()).hexdigest()[:16]
    return f"zotero-file:{record.source}:{record.source_id}#{digest}"


def _item_key(ref: str, item: ZoteroItem) -> str:
    return f"zotero:{ref}@v{item.version}" if item.version is not None else f"zotero:{ref}#{item.content_hash()[:16]}"


@dataclass
class _Session:
    writer: KnowledgeWriter
    seen: dict[str, str] = field(default_factory=dict)

    def write(self, key: str, record: BibRecord, idempotency_key: str) -> ImportItemResult:
        if idempotency_key in self.seen:  # same item twice in one import: written once
            return ImportItemResult(key, "reused", self.seen[idempotency_key])
        wid, created = self.writer.upsert_work(record, idempotency_key=idempotency_key)
        self.seen[idempotency_key] = wid
        return ImportItemResult(key, "created" if created else "reused", wid)


@dataclass
class _Fetched:
    items: dict[str, ZoteroItem] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # library -> scrubbed reason


class ZoteroWebImporter:
    spec = DataSourceSpec(
        "zotero_web",
        "0.1.0",
        "bibliographic",
        "Import selected Zotero items via the Zotero Web API v3 (metadata only; attachments never downloaded)",
        hosts=(HOST,),
        settings_schema=SETTINGS_SCHEMA,
    )

    def _fetch(self, http: HttpContext, refs: list[str | None], secret: str | None) -> _Fetched:
        out = _Fetched()
        by_lib: dict[str, list[str]] = {}
        for ref in sorted({r for r in refs if r}):
            lib, _, key = ref.rpartition("/")
            by_lib.setdefault(lib, []).append(key)
        for lib, keys in by_lib.items():
            try:
                objs = fetch_items(http, lib, keys, secret)
            except (ZoteroApiError, HttpDenied, HttpUnavailable) as e:
                retry = " (temporary; retry later)" if isinstance(e, HttpUnavailable) else ""
                out.errors[lib] = scrub(f"Zotero API error for {lib}: {e}{retry}", secret)
                continue
            for obj in objs:
                try:
                    item = item_from_api(obj)
                except ValueError:
                    continue
                if item.zotero_ref:
                    out.items[item.zotero_ref] = item
        return out

    def _one(self, record: BibRecord, ref: str | None, fetched: _Fetched, session: _Session) -> ImportItemResult:
        key = f"{record.source}:{record.source_id}"
        if ref is None:
            if record.source in FILE_SOURCES:
                return session.write(key, record, _file_key(record))
            return ImportItemResult(key, "rejected", reason="record carries no Zotero library/item key")
        lib = ref.rpartition("/")[0]
        if lib in fetched.errors:
            return ImportItemResult(key, "rejected", reason=fetched.errors[lib])
        item = fetched.items.get(ref)
        if item is None:
            return ImportItemResult(
                key,
                "rejected",
                reason="item not returned by the Zotero API (deleted, moved or not readable with these credentials)",
            )
        if item.item_type in NOT_WORKS:
            return ImportItemResult(key, "rejected", reason=NOT_WORKS[item.item_type or ""])
        if not item.title:
            return ImportItemResult(key, "rejected", reason="Zotero item has no title")
        return session.write(key, to_record(item, self.spec.name), _item_key(ref, item))

    def import_records(
        self, records: tuple[BibRecord, ...], writer: KnowledgeWriter, http: HttpContext, settings: Mapping[str, Any]
    ) -> list[ImportItemResult]:
        secret = str(settings["api_key"]) if settings.get("api_key") else None
        refs = [reference(r, settings) for r in records]
        fetched = self._fetch(http, refs, secret)
        session = _Session(writer)
        return [self._one(r, ref, fetched, session) for r, ref in zip(records, refs, strict=True)]
