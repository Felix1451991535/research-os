"""K8 Exporter contract (entry-point group `researchos.exporters`).

Input is an immutable ReportSnapshot whose inline spans keep citations and numbers STRUCTURED (cite / metric spans),
plus a read-only ExportContext that resolves them. Final exports must fail on unresolved citations or numbers;
draft mode may warn. Exporters write only inside ctx.out_dir; the host verifies paths and hashes every file.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from .datasources import BibRecord


@dataclass(frozen=True)
class TextSpan:
    text: str
    kind: Literal["text"] = "text"


@dataclass(frozen=True)
class CiteSpan:
    citekey: str
    kind: Literal["cite"] = "cite"


@dataclass(frozen=True)
class MetricSpan:
    run_id: str
    name: str
    split: str
    kind: Literal["metric"] = "metric"


Span = TextSpan | CiteSpan | MetricSpan


@dataclass(frozen=True)
class Block:
    kind: Literal["heading", "paragraph", "table", "figure", "list"]
    spans: tuple[Span, ...] = ()
    level: int = 1                                 # headings
    rows: tuple[tuple[tuple[Span, ...], ...], ...] = ()   # tables: rows of cells of spans
    artifact_id: str | None = None                 # figures
    caption: tuple[Span, ...] = ()
    claim_id: str | None = None                    # paragraph-level claim id (evidence links)


@dataclass(frozen=True)
class ReportSnapshot:
    doc_id: str
    revision: int
    title: str
    language: str                                  # "en" | "zh" | "mixed"
    blocks: tuple[Block, ...]
    kind: str = "report"                           # report | answer | manuscript | ...


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    version_id: str
    page_index: int
    quote: str
    trust: str


@dataclass(frozen=True)
class CitationEntry:
    citekey: str
    record: BibRecord
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class MetricValue:
    value: float
    unit: str | None
    verification: str                              # recomputed | invariant_checked | self_reported | ...


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    mime: str
    sha256: str
    run_id: str | None
    path: Path                                     # read-only file inside the host data dir


@dataclass(frozen=True)
class Diagnostic:
    severity: Literal["info", "warning", "error"]
    code: str                                      # unresolved_citation | unresolved_metric | missing_artifact | ...
    message: str
    ref: str | None = None


@dataclass(frozen=True)
class ExportFile:
    rel_path: str
    mime: str


@dataclass(frozen=True)
class ExportResult:
    files: tuple[ExportFile, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class ExporterSpec:
    name: str
    version: str
    doc_kinds: tuple[str, ...]
    mime_types: tuple[str, ...]
    description: str
    options_schema: Mapping[str, Any] = field(default_factory=dict)


class ExportContext(Protocol):
    mode: Literal["draft", "final"]

    @property
    def out_dir(self) -> Path: ...

    def citation(self, citekey: str) -> CitationEntry | None: ...

    def metric(self, run_id: str, name: str, split: str) -> MetricValue | None: ...

    def artifact(self, artifact_id: str) -> ArtifactRef | None: ...


class Exporter(Protocol):
    spec: ExporterSpec

    def export(self, snapshot: ReportSnapshot, ctx: ExportContext, options: Mapping[str, Any]) -> ExportResult: ...
