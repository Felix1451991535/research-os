"""K7 DataSource contract (entry-point group `researchos.datasources`).

Kinds:
- BibliographicSource: read-only search returning normalized BibRecords (no writes; imports go through Importer).
  Optional capabilities (core 1.4), detected by the host when the same object also implements them:
  CitationGraphSource (backward references / forward citations) and RetractionSource (retraction status).
- OaResolver (spec.kind "oa_resolver", core 1.4): DOI -> open-access PDF location with its license statement.
- DatasetProvider: builds a local dataset (download + convert) and returns a DatasetDescriptor; the host verifies
  every file hash and registers it (K1 Dataset). Expected upstream checksums must come from settings/manifest
  provenance, never be invented; an absent expected checksum is reported, not silently accepted.

Entry-point object: an INSTANCE implementing the protocol, or a zero-arg factory returning one (or a list).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from .files import LocalReadContext
from .http import HttpContext

IdScheme = Literal["doi", "pmid", "pmcid", "arxiv", "isbn", "cnki", "openalex", "s2", "zotero", "other"]


@dataclass(frozen=True)
class BibRecord:
    """Normalized bibliographic metadata. Unknown fields are None (never guessed). identifiers are normalized
    (doi lower-case without URL prefix; arXiv without version suffix unless the version is meaningful)."""
    source: str                                    # datasource name
    source_id: str                                 # id within that source
    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    identifiers: tuple[tuple[IdScheme, str], ...] = ()
    url: str | None = None
    oa_pdf_url: str | None = None
    license: str | None = None                     # SPDX-like id when known (e.g. "CC-BY-4.0")
    license_source: str | None = None              # where the license statement came from
    kind: str = "article"                          # article | review | preprint | thesis | patent | ...


@dataclass(frozen=True)
class SearchQuery:
    text: str
    filters: Mapping[str, str] = field(default_factory=dict)   # e.g. {"from_year": "2020", "type": "review"}
    page_size: int = 25
    cursor: str | None = None


@dataclass(frozen=True)
class SearchPage:
    records: tuple[BibRecord, ...]
    next_cursor: str | None = None
    total: int | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataSourceSpec:
    name: str                                      # unique, [a-z0-9_]
    version: str
    kind: Literal["bibliographic", "dataset", "oa_resolver"]
    description: str
    hosts: tuple[str, ...] = ()                    # network hosts required (must match manifest capabilities)
    settings_schema: Mapping[str, Any] = field(default_factory=dict)


class BibliographicSource(Protocol):
    spec: DataSourceSpec

    def search(self, query: SearchQuery, http: HttpContext, settings: Mapping[str, Any]) -> SearchPage: ...


class CitationGraphSource(Protocol):
    """Optional (core 1.4). `references` = works cited BY the record (backward chaining); `cited_by` = works citing
    it (forward chaining). Unknown record -> empty page with a warning, never a guess."""
    spec: DataSourceSpec

    def references(self, record: BibRecord, http: HttpContext, settings: Mapping[str, Any],
                   cursor: str | None = None, page_size: int = 25) -> SearchPage: ...

    def cited_by(self, record: BibRecord, http: HttpContext, settings: Mapping[str, Any],
                 cursor: str | None = None, page_size: int = 25) -> SearchPage: ...


@dataclass(frozen=True)
class RetractionStatus:
    identifier: tuple[IdScheme, str]
    retracted: bool | None                         # None = the source does not know this work (never assumed False)
    source: str
    notice_url: str | None = None


class RetractionSource(Protocol):
    """Optional (core 1.4): retraction status for up to 50 identifiers per call."""
    spec: DataSourceSpec

    def retraction_status(self, identifiers: tuple[tuple[IdScheme, str], ...], http: HttpContext,
                          settings: Mapping[str, Any]) -> tuple[RetractionStatus, ...]: ...


@dataclass(frozen=True)
class OaLocation:
    pdf_url: str
    license: str | None                            # SPDX-like id when explicitly stated upstream, else None
    license_source: str | None
    version: str | None = None                     # e.g. publishedVersion | acceptedVersion | submittedVersion
    host_type: str | None = None                   # publisher | repository


class OaResolver(Protocol):
    """spec.kind == "oa_resolver" (core 1.4). Returns None when no OA copy is known."""
    spec: DataSourceSpec

    def resolve(self, doi: str, http: HttpContext, settings: Mapping[str, Any]) -> OaLocation | None: ...


@dataclass(frozen=True)
class DatasetFile:
    path: str                                      # relative to the dataset directory
    sha256: str


@dataclass(frozen=True)
class DatasetDescriptor:
    """What the host registers. `table` is an .npz with arrays X [N,...], y [N], group [N], sample_id [N]
    (see WINDOW_TABLE_ARRAYS); group = the leakage unit (e.g. subject)."""
    name: str
    version: str
    source: str
    license: str
    group_key: str
    table: str
    files: tuple[DatasetFile, ...]
    overlapping_windows: bool = False
    expected_source_sha256: str | None = None      # from settings/upstream metadata; None => reported as unverified
    observed_source_sha256: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)  # e.g. channels, units, sampling_rate_hz, label_map
    egress: str = "local_only"  # core 1.7, one of EGRESS_POLICIES; fail closed when a provider says nothing


# core 1.7 (ADR-159): may content derived from this dataset reach a cloud model provider? local_only: never (e.g.
# credentialed PhysioNet data, DICOM); cloud_ok: yes; cloud_redacted: only after a redaction step, which does not
# exist yet, so the gateway treats it as local_only.
EGRESS_POLICIES: tuple[str, ...] = ("local_only", "cloud_redacted", "cloud_ok")


WINDOW_TABLE_ARRAYS: tuple[str, ...] = ("X", "y", "group", "sample_id")


class DatasetBuildContext(Protocol):
    """Host-provided: an empty directory owned by this build, HTTP access, cancellation, and (core 1.5) read-only
    access to owner file roots when the plugin manifest declares `files:read` (else None)."""

    @property
    def out_dir(self) -> Path: ...

    @property
    def http(self) -> HttpContext: ...

    @property
    def local(self) -> LocalReadContext | None: ...

    def cancel_requested(self) -> bool: ...

    def progress(self, stage: str, done: int | None = None, total: int | None = None) -> None: ...


class DatasetProvider(Protocol):
    spec: DataSourceSpec

    def build(self, ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> DatasetDescriptor: ...
