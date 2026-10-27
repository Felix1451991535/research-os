"""Doc body (Markdown subset) -> structured ReportSnapshot (K8). Placeholders become structured spans:
`{{cite:<citekey>}}` -> CiteSpan, `{{metric:<run_id>/<name>/<split>}}` -> MetricSpan,
`{{evidence:<id>}}` -> EvidenceSpan (host-only; resolved to its work's CiteSpan + page for exporters),
`{{lit:<text>}}` -> literal text the author declares is not a data value (exempt from the number gate).
Blocks: `#`..`######` headings, `- `/`* `/`1. ` list items, `|` table rows, `![caption](artifact:<id>)` figures,
blank-line separated paragraphs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...contracts.exporters import Block, CiteSpan, MetricSpan, ReportSnapshot, Span, TextSpan

PLACEHOLDER = re.compile(r"\{\{(cite|metric|evidence|lit):([^{}]+)\}\}")
FIGURE = re.compile(r"^!\[(?P<cap>[^\]]*)\]\(artifact:(?P<id>[\w-]+)\)\s*$")
LIST = re.compile(r"^\s*(?:[-*]|\d{1,3}[.)])\s+")


@dataclass(frozen=True)
class EvidenceSpan:
    evidence_id: str
    kind: str = "evidence"


@dataclass(frozen=True)
class LiteralSpan:
    text: str
    kind: str = "lit"


SourceSpan = TextSpan | CiteSpan | MetricSpan | EvidenceSpan | LiteralSpan


def spans(text: str) -> tuple[SourceSpan, ...]:
    out: list[SourceSpan] = []
    pos = 0
    for m in PLACEHOLDER.finditer(text):
        if m.start() > pos:
            out.append(TextSpan(text[pos : m.start()]))
        kind, val = m.group(1), m.group(2).strip()
        if kind == "cite":
            out += [CiteSpan(k.strip()) for k in val.split(";") if k.strip()]
        elif kind == "metric":
            parts = val.split("/")
            out.append(MetricSpan(*parts) if len(parts) == 3 else TextSpan(m.group(0)))  # malformed -> flagged
        elif kind == "evidence":
            out.append(EvidenceSpan(val))
        else:
            out.append(LiteralSpan(val))
        pos = m.end()
    if pos < len(text):
        out.append(TextSpan(text[pos:]))
    return tuple(out)


@dataclass(frozen=True)
class SourceBlock:
    kind: str
    spans: tuple[SourceSpan, ...] = ()
    level: int = 1
    rows: tuple[tuple[tuple[SourceSpan, ...], ...], ...] = ()
    artifact_id: str | None = None
    caption: tuple[SourceSpan, ...] = ()


def _structural(s: str) -> bool:
    return s.startswith("|") or bool(re.match(r"^#{1,6}\s", s) or FIGURE.match(s) or LIST.match(s))


def _close_table(out: list[SourceBlock], table: list[tuple[tuple[SourceSpan, ...], ...]]) -> None:
    if table:
        out.append(SourceBlock("table", rows=tuple(table)))
        table.clear()


def _block(s: str, out: list[SourceBlock], table: list[tuple[tuple[SourceSpan, ...], ...]]) -> None:
    if s.startswith("|"):
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):  # skip the |---| separator
            table.append(tuple(spans(c) for c in cells))
        return
    _close_table(out, table)
    if m := re.match(r"^(#{1,6})\s+(.*)$", s):
        out.append(SourceBlock("heading", spans(m.group(2)), level=len(m.group(1))))
    elif m := FIGURE.match(s):
        out.append(SourceBlock("figure", artifact_id=m.group("id"), caption=spans(m.group("cap"))))
    else:
        out.append(SourceBlock("list", spans(LIST.sub("", s, count=1))))


def parse(body: str) -> list[SourceBlock]:
    out: list[SourceBlock] = []
    para: list[str] = []
    table: list[tuple[tuple[SourceSpan, ...], ...]] = []
    for line in body.splitlines():
        s = line.strip()
        if not s or _structural(s):
            if para:
                out.append(SourceBlock("paragraph", spans(" ".join(para))))
                para.clear()
            if s:
                _block(s, out, table)
            else:
                _close_table(out, table)
            continue
        _close_table(out, table)
        para.append(s)
    if para:
        out.append(SourceBlock("paragraph", spans(" ".join(para))))
    _close_table(out, table)
    return out


def to_snapshot(doc_id: str, revision: int, title: str, kind: str, blocks: list[SourceBlock],
                evidence_cite: dict[str, tuple[str, int]]) -> ReportSnapshot:
    """Exporter view: evidence -> cite of its work + page label; literals -> text."""

    def conv(ss: tuple[SourceSpan, ...]) -> tuple[Span, ...]:
        res: list[Span] = []
        for s in ss:
            if isinstance(s, EvidenceSpan):
                key, page = evidence_cite.get(s.evidence_id, (f"evidence:{s.evidence_id}", -1))
                res.append(CiteSpan(key))
                if page >= 0:
                    res.append(TextSpan(f" (PDF p. {page + 1})"))  # PDF page index, not the printed label (review #66)
            elif isinstance(s, LiteralSpan):
                res.append(TextSpan(s.text))
            else:
                res.append(s)
        return tuple(res)

    out = tuple(Block(b.kind, conv(b.spans), b.level, tuple(tuple(conv(c) for c in r) for r in b.rows),  # type: ignore[arg-type]
                      b.artifact_id, conv(b.caption)) for b in blocks)
    lang = "zh" if any("一" <= ch <= "鿿" for ch in title) else "en"
    return ReportSnapshot(doc_id, revision, title, lang, out, kind)
