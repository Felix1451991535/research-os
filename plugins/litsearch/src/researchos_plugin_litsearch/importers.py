"""Selection-scoped imports and strict UTF-8 RIS/BibTeX file parsing."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any, cast
from urllib.parse import urlsplit

import bibtexparser
from researchos.contracts.datasources import BibRecord, DataSourceSpec
from researchos.contracts.http import HttpContext, HttpDenied, HttpUnavailable
from researchos.contracts.importers import FileImportRequest, ImportItemResult, KnowledgeWriter

from .normalize import doi, text, year

PDF_HOSTS = ("arxiv.org", "pmc.ncbi.nlm.nih.gov", "api.crossref.org")
OPEN = {"CC-BY", "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0"}


class SelectionImporter:
    spec = DataSourceSpec(
        "lit_selection", "0.1.0", "bibliographic", "Selected metadata and explicitly licensed PDFs", PDF_HOSTS
    )

    def import_records(
        self, records: tuple[BibRecord, ...], writer: KnowledgeWriter, http: HttpContext, settings: Mapping[str, Any]
    ) -> list[ImportItemResult]:
        result = []
        for record in records:
            key = f"{record.source}:{record.source_id}"
            if not record.title.strip() or not record.source_id:
                result.append(ImportItemResult(key, "rejected", reason="Missing title or source identity"))
                continue
            digest = hashlib.sha256(json.dumps(asdict(record), sort_keys=True).encode()).hexdigest()
            work, created = writer.upsert_work(record, idempotency_key=f"lit:work:{digest}")
            version, reason = self._pdf(record, work, digest, writer, http)
            result.append(ImportItemResult(key, "created" if created else "reused", work, version, reason))
        return result

    def _pdf(
        self, record: BibRecord, work: str, digest: str, writer: KnowledgeWriter, http: HttpContext
    ) -> tuple[str | None, str | None]:
        url = record.oa_pdf_url
        if not url or record.license not in OPEN or not record.license_source:
            return None, "Metadata only: no PDF with explicit supported license provenance"
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in PDF_HOSTS:
            return None, "Metadata only: PDF host is outside declared capabilities"
        try:
            response = http.get(url)
        except (HttpDenied, HttpUnavailable):
            return None, "Metadata only: host refused or could not fetch PDF"
        if (
            response.status != 200
            or not response.body.startswith(b"%PDF-")
            or urlsplit(response.url).hostname not in PDF_HOSTS
            or urlsplit(response.url).scheme != "https"
        ):
            return None, "Metadata only: invalid PDF response"
        version = writer.attach_original(
            work, response.body, f"{digest}.pdf", license=record.license, idempotency_key=f"lit:pdf:{digest}"
        )
        return version, None


def _record(fields: dict[str, list[str]], origin: str, identity: str) -> BibRecord:
    def first(*tags: str) -> str | None:
        return next((v for tag in tags if (v := text((fields.get(tag) or [""])[0]))), None)

    title = first("TI", "T1")
    if not title:
        raise ValueError("Bibliographic file record has no title")
    d = doi(first("DO"))
    return BibRecord(
        origin,
        identity,
        title,
        tuple(fields.get("AU", fields.get("A1", []))),
        year(first("PY", "Y1")),
        first("JO", "JF", "T2"),
        first("AB"),
        (("doi", d),) if d else (),
        first("UR"),
    )


class RisImporter:
    spec = DataSourceSpec("lit_ris", "0.1.0", "bibliographic", "UTF-8 RIS including CNKI RIS exports")

    def parse_file(self, req: FileImportRequest) -> list[BibRecord]:
        current: dict[str, list[str]] = {}
        records: list[BibRecord] = []
        last: str | None = None
        for line in req.data.decode("utf-8-sig").splitlines():
            match = re.match(r"^([A-Z0-9]{2})  - ?(.*)$", line)
            if not match:
                if line.strip() and last:
                    current[last][-1] += " " + line.strip()
                elif line.strip():
                    raise ValueError("Malformed RIS line")
                continue
            tag, value = match.groups()
            if tag == "ER":
                ident = hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest()
                records.append(_record(current, self.spec.name, ident))
                current, last = {}, None
            else:
                if tag == "TY" and current:
                    raise ValueError("RIS record missing ER terminator")
                current.setdefault(tag, []).append(value.strip())
                last = tag
        if current:
            raise ValueError("Unterminated RIS record")
        return records


class BibtexImporter:
    spec = DataSourceSpec("lit_bibtex", "0.1.0", "bibliographic", "UTF-8 BibTeX file parser")

    def parse_file(self, req: FileImportRequest) -> list[BibRecord]:
        load = cast(Callable[[str], Any], bibtexparser.loads)
        database = load(req.data.decode("utf-8-sig"))
        records = []
        for entry in database.entries:
            fields = {
                ris: [entry[bib]]
                for bib, ris in (
                    ("title", "TI"),
                    ("year", "PY"),
                    ("journal", "JO"),
                    ("doi", "DO"),
                    ("abstract", "AB"),
                    ("url", "UR"),
                )
                if bib in entry
            }
            fields["AU"] = re.split(r"\s+and\s+", entry["author"]) if entry.get("author") else []
            records.append(_record(fields, self.spec.name, str(entry["ID"])))
        if req.data.strip() and not records:
            raise ValueError("No supported BibTeX entries")
        return records
