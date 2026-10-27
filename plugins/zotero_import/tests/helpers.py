"""Offline doubles + SYNTHETIC fixture loaders for the zotero_import tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from researchos.contracts.datasources import BibRecord
from researchos.contracts.http import HttpResponse
from researchos.sdk.testing import FakeHttp, InMemoryKnowledgeWriter

FIX = Path(__file__).parents[1] / "fixtures"
API = "https://api.zotero.org"
LIB = "users/1234567"
FAKE_KEY = "SYNTHETIC-TEST-KEY-0000-not-a-real-secret"
SETTINGS = {"library_type": "user", "library_id": "1234567", "api_key": FAKE_KEY}
KEYS = ("ABCD2345", "EFGH6789", "NPQR2345", "WXYZ2345", "ZZZZ2345")


def page_url(library: str, keys: tuple[str, ...], start: int) -> str:
    params = {"format": "json", "itemKey": ",".join(sorted(keys)), "limit": "50", "start": str(start)}
    return f"{API}/{library}/items?" + urlencode(sorted(params.items()))


def api_page(url: str, name: str, headers: Mapping[str, str]) -> HttpResponse:
    return HttpResponse(200, {"content-type": "application/json", **headers}, (FIX / name).read_bytes(), url)


def two_page_routes(link: bool = True) -> dict[str, HttpResponse | Callable[[str], HttpResponse]]:
    p1, p2 = page_url(LIB, KEYS, 0), page_url(LIB, KEYS, 2)
    h1 = {"Total-Results": "4", **({"Link": f'<{p2}>; rel="next", <{p2}>; rel="last"'} if link else {})}
    return {
        p1: api_page(p1, "api_items_page1.json", h1),
        p2: api_page(p2, "api_items_page2.json", {"Total-Results": "4"}),
    }


class RecordingHttp(FakeHttp):
    """FakeHttp that also records request headers (to prove the key travels only as a header)."""

    def __init__(
        self,
        routes: Mapping[str, HttpResponse | Callable[[str], HttpResponse]],
        allowed: tuple[str, ...] = ("api.zotero.org",),
    ) -> None:
        super().__init__(routes, allowed)
        self.headers: list[dict[str, str]] = []

    def get(
        self, url: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None
    ) -> HttpResponse:
        self.headers.append(dict(headers or {}))
        return super().get(url, params=params, headers=headers)


@dataclass
class CountingWriter(InMemoryKnowledgeWriter):
    upserts: list[str] = field(default_factory=list)

    def upsert_work(self, record: BibRecord, *, idempotency_key: str) -> tuple[str, bool]:
        self.upserts.append(idempotency_key)
        return super().upsert_work(record, idempotency_key=idempotency_key)


def csl_records() -> list[BibRecord]:
    from researchos.contracts.importers import FileImportRequest

    from researchos_plugin_zotero_import.csljson import ZoteroCslJsonImporter

    data = (FIX / "zotero_export.csl.json").read_bytes()
    return ZoteroCslJsonImporter().parse_file(FileImportRequest("export.json", data))


def web_record(key: str, title: str = "selected item") -> BibRecord:
    return BibRecord("zotero_web", f"{LIB}/{key}", title)


def no_empty_strings(record: Any) -> bool:
    return all(v != "" for v in vars(record).values())
