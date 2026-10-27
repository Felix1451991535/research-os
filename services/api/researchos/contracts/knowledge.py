"""K7 Knowledge contract: parsers, data sources, retrievers. Parsers are pure (bytes in, pages out)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class PageQuality(StrEnum):
    OK = "ok"
    NEEDS_PROOFREADING = "needs_proofreading"
    EMPTY = "empty"          # no extractable text (scanned) -> OCR needed
    GARBLED = "garbled"


@dataclass(frozen=True)
class ParsedChunk:
    char_start: int
    char_end: int
    text: str
    section_role: str | None = None
    chunk_kind: str = "text"
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class ParsedPage:
    page_index: int
    text: str
    width: float | None
    height: float | None
    rotation: int
    quality: PageQuality
    flags: tuple[str, ...] = ()
    page_label: str | None = None
    chunks: tuple[ParsedChunk, ...] = ()


@dataclass(frozen=True)
class ParseResult:
    parser_version: str
    pages: tuple[ParsedPage, ...]
    metadata: dict[str, str] = field(default_factory=dict)  # e.g. title / doi found in the file
    warnings: tuple[str, ...] = ()


class ParseError(Exception):
    """Unparseable input. `code` is stable (corrupt|encrypted|too_many_pages|unsupported); `recovery` is advice."""

    def __init__(self, code: str, message: str, recovery: str) -> None:
        super().__init__(message)
        self.code, self.recovery = code, recovery


class Parser(Protocol):
    name: str
    version: str
    mime_types: tuple[str, ...]

    def parse(self, data: bytes, *, max_pages: int) -> ParseResult: ...


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    version_id: str
    work_id: str
    title: str
    page_index: int
    page_label: str | None
    char_start: int
    char_end: int
    text: str
    score: float
    section_role: str | None
    quality: str


class Retriever(Protocol):
    name: str

    def search(self, query: str, *, project_id: str | None, limit: int) -> list[SearchHit]: ...
