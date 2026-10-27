"""Identifier extraction + normalization (DOI, arXiv) from document text/metadata."""
from __future__ import annotations

import re

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
ARXIV_RE = re.compile(r"\barXiv:\s?(\d{4}\.\d{4,5})(v\d+)?", re.IGNORECASE)


def normalize_doi(doi: str) -> str:
    d = doi.strip().lower()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", d)
    return d.rstrip(".,;)]}")


def find_identifiers(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    m = DOI_RE.search(text)
    if m:
        out.append(("doi", normalize_doi(m.group(1))))
    a = ARXIV_RE.search(text)
    if a:
        out.append(("arxiv", a.group(1)))
    return out


def guess_title(first_page_text: str, pdf_title: str | None) -> str:
    if pdf_title and len(pdf_title.strip()) > 5 and not pdf_title.lower().startswith(("microsoft word", "untitled")):
        return pdf_title.strip()[:300]
    for line in first_page_text.splitlines():
        if len(line.strip()) > 10:
            return line.strip()[:300]
    return ""
