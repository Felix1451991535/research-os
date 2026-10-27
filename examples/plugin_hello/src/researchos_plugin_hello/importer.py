"""Importers: OA PDFs only with an explicit open license; RIS file parser (pure)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from researchos.contracts.datasources import BibRecord, DataSourceSpec
from researchos.contracts.http import HttpContext, HttpDenied
from researchos.contracts.importers import FileImportRequest, ImportItemResult, KnowledgeWriter

OPEN = {"CC-BY-4.0", "CC0-1.0"}


class OpenAccessImporter:
    spec = DataSourceSpec(
        "hello_open_importer", "0.1.0", "bibliographic", "metadata + open-licensed PDFs", hosts=("api.crossref.org",)
    )

    def import_records(
        self, records: tuple[BibRecord, ...], writer: KnowledgeWriter, http: HttpContext, settings: Mapping[str, Any]
    ) -> list[ImportItemResult]:
        out = []
        for r in records:
            key = f"{r.source}:{r.source_id}"
            if not r.title:
                out.append(ImportItemResult(key, "rejected", reason="record has no title"))
                continue
            wid, created = writer.upsert_work(r, idempotency_key=f"work:{key}")
            vid = None
            if r.oa_pdf_url and r.license in OPEN:
                try:
                    pdf = http.get(r.oa_pdf_url)
                    if pdf.status == 200 and pdf.body.startswith(b"%PDF"):
                        vid = writer.attach_original(
                            wid,
                            pdf.body,
                            f"{r.source_id.replace('/', '_')}.pdf",
                            license=r.license,
                            idempotency_key=f"pdf:{key}",
                        )
                except HttpDenied:
                    vid = None  # host not granted: metadata-only import
            out.append(ImportItemResult(key, "created" if created else "reused", wid, vid))
        return out


def _first(cur: dict[str, list[str]], tag: str) -> str | None:
    vals = cur.get(tag)
    return vals[0] if vals else None


def _ris_record(cur: dict[str, list[str]]) -> BibRecord:
    doi, title = _first(cur, "DO"), cur["TI"][0]
    year = (_first(cur, "PY") or "")[:4]
    return BibRecord("hello_ris", doi or title, title, tuple(cur.get("AU", [])),
                     int(year) if year.isdigit() else None, _first(cur, "JO"),
                     identifiers=(("doi", doi.lower()),) if doi else ())


class RisFileImporter:
    spec = DataSourceSpec("hello_ris", "0.1.0", "bibliographic", "RIS file parser (e.g. CNKI/EndNote exports)")

    def parse_file(self, req: FileImportRequest) -> list[BibRecord]:
        recs: list[BibRecord] = []
        cur: dict[str, list[str]] = {}
        for line in req.data.decode("utf-8-sig", errors="replace").splitlines():
            tag, _, val = line.partition("  - ")
            if tag == "ER":
                if cur.get("TI"):
                    recs.append(_ris_record(cur))
                cur = {}
            elif val:
                cur.setdefault(tag.strip(), []).append(val.strip())
        return recs
