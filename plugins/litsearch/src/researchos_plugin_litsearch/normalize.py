"""Pure normalization and provenance-preserving result-view grouping."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from researchos.contracts.datasources import BibRecord


def text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def doi(value: Any) -> str | None:
    v = text(value)
    if v is None:
        return None
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", v, flags=re.I).lower()


def year(value: Any) -> int | None:
    s = str(value)
    return int(s[:4]) if re.match(r"^\d{4}(?:\D|$)", s) and 1500 < int(s[:4]) < 2200 else None


def arxiv(value: str) -> str:
    return re.sub(r"v\d+$", "", re.sub(r"^(?:https?://arxiv.org/abs/|arxiv:)", "", value, flags=re.I))


def open_license(value: Any) -> str | None:
    v = (text(value) or "").lower().rstrip("/")
    licenses = {"cc-by": "CC-BY", "cc-by-4.0": "CC-BY-4.0", "cc0": "CC0-1.0"}
    licenses.update(
        {
            "https://creativecommons.org/licenses/by/4.0": "CC-BY-4.0",
            "http://creativecommons.org/licenses/by/4.0": "CC-BY-4.0",
            "https://creativecommons.org/publicdomain/zero/1.0": "CC0-1.0",
        }
    )
    return licenses.get(v)


T = TypeVar("T")


def collect(
    items: Iterable[T], parse: Callable[[T], BibRecord], label: str
) -> tuple[tuple[BibRecord, ...], tuple[str, ...]]:
    """One malformed upstream record is skipped and reported; it never discards the rest of the page.
    A malformed response envelope still raises (handled by the caller)."""
    records: list[BibRecord] = []
    skipped = 0
    for item in items:
        try:
            records.append(parse(item))
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            skipped += 1
    return tuple(records), ((f"{label}: skipped {skipped} malformed record(s)",) if skipped else ())


@dataclass(frozen=True)
class ResultGroup:
    """Every original record retained; grouping never mutates core Works."""

    records: tuple[BibRecord, ...]


def _compatible(a: BibRecord, b: BibRecord) -> bool:
    ai, bi = dict(a.identifiers), dict(b.identifiers)
    hard = ("doi", "pmid", "arxiv")
    if any(k in ai and k in bi and ai[k] != bi[k] for k in hard):
        return False
    for k in hard:
        if k in ai and k in bi:
            return ai[k] == bi[k]
    return " ".join(a.title.casefold().split()) == " ".join(b.title.casefold().split())


def group_results(records: tuple[BibRecord, ...]) -> tuple[ResultGroup, ...]:
    groups: list[list[BibRecord]] = []
    for record in records:
        match = next((g for g in groups if all(_compatible(record, r) for r in g)), None)
        if match is None:
            groups.append([record])
        else:
            match.append(record)
    return tuple(ResultGroup(tuple(g)) for g in groups)
