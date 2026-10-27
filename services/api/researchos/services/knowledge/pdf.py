"""Text-PDF parser (pypdf, BSD). Per page: text, approximate bboxes, quality flags, section roles, chunks.

Honesty rules: pages without extractable text are flagged EMPTY (OCR needed, never guessed); suspected
multi-column order or garbled text -> NEEDS_PROOFREADING; bboxes are approximate (flag 'bbox_approx').
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ...contracts.knowledge import PageQuality, ParsedChunk, ParsedPage, ParseError, ParseResult

PARSER_VERSION = "pypdf-text/3"  # /2: captions are own chunks; /3: unterminated caption ends at paragraph
CHUNK_TARGET = 700
_HEADING = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(abstract|introduction|background|related work|methods?|"
                      r"materials and methods|experiments?|results|discussion|conclusions?|references|"
                      r"bibliography|acknowledg(?:e)?ments)\b", re.IGNORECASE)
_ROLE = {"abstract": "abstract", "introduction": "introduction", "background": "introduction",
         "related work": "introduction", "method": "methods", "methods": "methods", "materials and methods": "methods",
         "experiment": "methods", "experiments": "methods", "results": "results", "discussion": "discussion",
         "conclusion": "discussion", "conclusions": "discussion", "references": "references",
         "bibliography": "references", "acknowledgments": "other", "acknowledgements": "other"}


@dataclass
class _Frag:
    text: str
    x: float
    y: float
    size: float


def _fragments(page: object) -> list[_Frag]:
    frags: list[_Frag] = []

    def visit(text: str, cm: list[float], tm: list[float], font: object, size: float) -> None:
        if text.strip():
            frags.append(_Frag(text, cm[4] + tm[4] * cm[0], cm[5] + tm[5] * cm[3], abs(size * tm[0]) or size))

    page.extract_text(visitor_text=visit)  # type: ignore[attr-defined]
    return frags


def _quality(text: str, frags: list[_Frag], width: float | None) -> tuple[PageQuality, list[str]]:
    flags: list[str] = []
    stripped = re.sub(r"\s", "", text)
    if len(stripped) < 20:
        return PageQuality.EMPTY, ["no_text_layer"]
    bad = sum(1 for c in stripped if c == "�" or (not c.isprintable()))
    if bad / len(stripped) > 0.05:
        return PageQuality.GARBLED, ["unmappable_glyphs"]
    if width and len(frags) > 20:
        right = sum(1 for f in frags if f.x > width * 0.48)
        left = sum(1 for f in frags if f.x < width * 0.35)
        if right > 0.3 * len(frags) and left > 0.3 * len(frags):
            flags.append("multi_column_possible")
    if flags:
        return PageQuality.NEEDS_PROOFREADING, flags
    return PageQuality.OK, flags


_CAPTION = re.compile(r"\s*(fig(ure)?|table)\.?\s*\d", re.IGNORECASE)


def _chunk_spans(text: str) -> list[tuple[int, int]]:
    """Split at blank lines, headings and caption starts; a caption chunk ends with its first sentence-ending line;
    otherwise group lines up to ~CHUNK_TARGET chars. Offsets index into `text`."""
    spans: list[tuple[int, int]] = []
    start = 0
    pos = 0
    in_caption = False
    for line in text.splitlines(keepends=True):
        end = pos + len(line)
        blank = not line.strip()
        cap = bool(_CAPTION.match(line))
        if (blank or end - start >= CHUNK_TARGET or _HEADING.match(line) or cap) and text[start:pos].strip():
            spans.append((start, pos))
            start = pos
        pos = end
        if blank or (_HEADING.match(line) and not cap):  # a caption without a final period ends at its paragraph
            in_caption = False
        in_caption = in_caption or cap
        if in_caption and line.rstrip().endswith((".", "。")):  # caption = label + first sentence
            spans.append((start, pos))
            start, in_caption = pos, False
        if blank:
            start = pos
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _bbox(chunk: str, frags: list[_Frag]) -> tuple[float, float, float, float] | None:
    hits = [f for f in frags if f.text.strip() and f.text.strip() in chunk]
    if not hits:
        return None
    return (min(f.x for f in hits), min(f.y for f in hits), max(f.x + f.size * 0.5 * len(f.text) for f in hits),
            max(f.y + f.size for f in hits))


class PdfParser:
    name = "pypdf"
    version = PARSER_VERSION
    mime_types = ("application/pdf",)

    def parse(self, data: bytes, *, max_pages: int) -> ParseResult:
        reader = self._open(data)
        if len(reader.pages) > max_pages:
            raise ParseError("too_many_pages", f"{len(reader.pages)} pages > limit {max_pages}",
                             "split the PDF or raise max_pdf_pages in settings")
        pages, role = [], None
        for i, page in enumerate(reader.pages):
            parsed, role = self._page(i, page, role)
            pages.append(parsed)
        return ParseResult(PARSER_VERSION, tuple(pages), self._meta(reader))

    @staticmethod
    def _open(data: bytes) -> PdfReader:
        if not data.startswith(b"%PDF"):
            raise ParseError("unsupported", "not a PDF file (missing %PDF header)", "export the document as PDF")
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ParseError("encrypted", "PDF is password-protected", "remove the password and re-import")
            _ = len(reader.pages)
        except ParseError:
            raise
        except (PdfReadError, ValueError, KeyError, TypeError) as e:
            raise ParseError("corrupt", f"PDF could not be read: {e}", "re-download the file; check it opens") from e
        return reader

    @staticmethod
    def _meta(reader: PdfReader) -> dict[str, str]:
        out: dict[str, str] = {}
        info: dict[str, object] = dict(reader.metadata or {})
        for k in ("/Title", "/Author", "/Subject"):
            v = info.get(k)
            if v:
                out[k.strip("/").lower()] = str(v)
        return out

    def _page(self, i: int, page: object, role: str | None) -> tuple[ParsedPage, str | None]:
        try:
            text = page.extract_text() or ""  # type: ignore[attr-defined]
            frags = _fragments(page)
        except Exception:  # one broken page must not lose the document
            return ParsedPage(i, "", None, None, 0, PageQuality.GARBLED, ("extract_failed",)), role
        box = page.mediabox  # type: ignore[attr-defined]
        width, height = float(box.width), float(box.height)
        quality, flags = _quality(text, frags, width)
        chunks = []
        for a, b in _chunk_spans(text):
            seg = text[a:b]
            m = _HEADING.match(seg)
            if m:
                key = m.group(1).lower()
                role = _ROLE.get(key, _ROLE.get(key.rstrip("s"), "other"))
            kind = "caption" if _CAPTION.match(seg) else "text"
            chunks.append(ParsedChunk(a, b, seg, role, kind, _bbox(seg, frags)))
        flags_t = tuple(flags) + (("bbox_approx",) if chunks else ())
        rot = int(getattr(page, "rotation", 0) or 0)
        return ParsedPage(i, text, width, height, rot, quality, flags_t, None, tuple(chunks)), role
