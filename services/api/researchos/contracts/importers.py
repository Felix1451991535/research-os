"""K7 Importer contract (entry-point group `researchos.importers`).

Importers turn SELECTED BibRecords (or user-exported files, e.g. CNKI RefWorks) into Works/document versions.
They never write the DB directly: the host passes a KnowledgeWriter whose operations are idempotent, evented and
provenance-tracked. The host verifies the K3 approval (subject `knowledge_import`, bound to the hash of the selected
records) BEFORE calling the importer; plugins never receive or check approval booleans.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .datasources import BibRecord, DataSourceSpec
from .http import HttpContext


@dataclass(frozen=True)
class ImportItemResult:
    key: str                                       # record key: "<source>:<source_id>"
    status: Literal["created", "reused", "rejected"]
    work_id: str | None = None
    version_id: str | None = None
    reason: str | None = None                      # required when rejected (e.g. "license not open")


class KnowledgeWriter(Protocol):
    """Host-provided. Hard-identifier dedupe (DOI > PMID > arXiv); title-only matches never merge Works."""

    def upsert_work(self, record: BibRecord, *, idempotency_key: str) -> tuple[str, bool]:
        """Returns (work_id, created)."""
        ...

    def attach_original(self, work_id: str, data: bytes, filename: str, *, license: str | None,
                        idempotency_key: str) -> str:
        """Stores bytes read-only (content-addressed), queues parsing; returns version_id."""
        ...


class Importer(Protocol):
    spec: DataSourceSpec                           # kind "bibliographic"; hosts needed for OA downloads

    def import_records(self, records: tuple[BibRecord, ...], writer: KnowledgeWriter, http: HttpContext,
                       settings: Mapping[str, Any]) -> list[ImportItemResult]: ...


@dataclass(frozen=True)
class FileImportRequest:
    filename: str
    data: bytes
    options: Mapping[str, Any] = field(default_factory=dict)


class FileImporter(Protocol):
    """Parses user-exported bibliographic files (RefWorks/RIS/BibTeX/EndNote) into BibRecords. Pure, no network."""
    spec: DataSourceSpec

    def parse_file(self, req: FileImportRequest) -> list[BibRecord]: ...
