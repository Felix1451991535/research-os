"""Identifier, year and license normalisation. Invalid or ambiguous input yields None, never a guess."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*|doi\s+)", re.I)
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_PREFIX = re.compile(r"^(?:arxiv:\s*|https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/)", re.I)
_ARXIV_NEW = re.compile(r"^\d{4}\.\d{4,5}$")
_ARXIV_OLD = re.compile(r"^[a-z][a-z\-]*(?:\.[A-Z]{2})?/\d{7}$")
_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
_EXTRA_LINE = re.compile(r"^\s*(DOI|PMID|PMCID|arXiv|ISBN)\s*:\s*(\S.*?)\s*$", re.I | re.M)


def norm_doi(value: str | None) -> str | None:
    if not value:
        return None
    s = _DOI_PREFIX.sub("", value.strip()).strip().lower()
    return s if _DOI.match(s) else None


def norm_pmid(value: str | None) -> str | None:
    s = (value or "").strip()
    s = re.sub(r"^pmid:\s*", "", s, flags=re.I)
    return s if re.fullmatch(r"[1-9]\d{0,8}", s) else None


def norm_pmcid(value: str | None) -> str | None:
    s = re.sub(r"^pmcid:\s*", "", (value or "").strip(), flags=re.I).upper()
    if re.fullmatch(r"PMC[1-9]\d*", s):
        return s
    return f"PMC{s}" if re.fullmatch(r"[1-9]\d*", s) else None


def norm_arxiv(value: str | None) -> str | None:
    s = _ARXIV_PREFIX.sub("", (value or "").strip()).removesuffix(".pdf")
    s = re.sub(r"v\d+$", "", s)
    return s if _ARXIV_NEW.match(s) or _ARXIV_OLD.match(s) else None


def _isbn_ok(s: str) -> bool:
    if re.fullmatch(r"\d{9}[\dX]", s):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
        return total % 11 == 0
    if re.fullmatch(r"97[89]\d{10}", s):
        return sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(s)) % 10 == 0
    return False


def norm_isbns(value: str | None) -> list[str]:
    """All checksum-valid ISBN-10/13 in a field such as '978-0-306-40615-7 0306406152'."""
    out: list[str] = []
    for tok in re.split(r"[\s,;]+", (value or "").upper()):
        s = tok.replace("-", "").removeprefix("ISBN").removeprefix(":")
        if _isbn_ok(s) and s not in out:
            out.append(s)
    return out


def extra_ids(extra: str | None) -> dict[str, list[str]]:
    """Identifier lines of Zotero's Extra field, e.g. 'PMID: 12345' (one per line)."""
    out: dict[str, list[str]] = {}
    for key, value in _EXTRA_LINE.findall(extra or ""):
        out.setdefault(key.lower(), []).append(value)
    return out


def year_of(text: str | None) -> int | None:
    """The year only if the text states exactly one plausible 4-digit year."""
    years = {int(y) for y in _YEAR.findall(text or "")}
    if len(years) != 1:
        return None
    y = years.pop()
    return y if 1500 < y < 2200 else None


_LICENSES = (
    (re.compile(r"\bcc[\s-]*by[\s-]*sa\b.*4\.0|attribution-sharealike 4\.0", re.I), "CC-BY-SA-4.0"),
    (re.compile(r"\bcc[\s-]*by\b(?![\s-]*(?:sa|nc|nd)).*4\.0|creative commons attribution 4\.0", re.I), "CC-BY-4.0"),
    (re.compile(r"\bcc0\b|public domain dedication", re.I), "CC0-1.0"),
)


def license_of(rights: str | None) -> str | None:
    """SPDX id for a clearly stated open license in Zotero's Rights field; anything else -> None."""
    if not rights:
        return None
    hits = {spdx for rx, spdx in _LICENSES if rx.search(rights)}
    return hits.pop() if len(hits) == 1 else None


def clean(value: object) -> str | None:
    """Stripped string, or None for missing/blank values (never '')."""
    if value is None:
        return None
    s = " ".join(str(value).split())
    return s or None


_CJK = re.compile(r"^[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff·]+$")


def person_name(given: object, family: object) -> str | None:
    """'Given Family'; CJK names keep family-name-first order without a space (e.g. 李明)."""
    g, f = clean(given), clean(family)
    if g and f and _CJK.match(g) and _CJK.match(f):
        return f + g
    return clean(" ".join(x for x in (g, f) if x))


def html_text(value: object) -> str | None:
    """Plain text of a Zotero note (HTML)."""
    if value is None:
        return None
    return clean(html.unescape(re.sub(r"<[^>]+>", " ", str(value))))


def unique(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return tuple(out)
