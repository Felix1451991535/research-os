"""Portable query filters (0.3.0): `author` (author watch) and `from_year` (catch-up searches). Anything else is
refused, never silently ignored. Each source maps them to its own syntax or reports them as unsupported."""

from __future__ import annotations

import re

from researchos.contracts.datasources import SearchQuery

SUPPORTED = ("author", "from_year")


def check(query: SearchQuery) -> None:
    unknown = sorted(set(query.filters) - set(SUPPORTED))
    if unknown:
        raise ValueError(f"unsupported filters {unknown}; supported: {list(SUPPORTED)}")
    fy = query.filters.get("from_year")
    if fy is not None and not re.fullmatch(r"(1[5-9]|20|21)\d\d", fy):
        raise ValueError("from_year must be a 4-digit year")
    author = query.filters.get("author")
    if author is not None and not author.strip():
        raise ValueError("author filter must be non-empty")


def author(query: SearchQuery) -> str | None:
    a = query.filters.get("author")
    return a.strip() if a else None


def from_year(query: SearchQuery) -> int | None:
    fy = query.filters.get("from_year")
    return int(fy) if fy else None
