"""One structured resolution pass, preserving all evidence and metric provenance."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from researchos.contracts.exporters import (
    Block,
    CitationEntry,
    CiteSpan,
    Diagnostic,
    ExportContext,
    MetricSpan,
    ReportSnapshot,
    Span,
)


@dataclass
class Resolved:
    blocks: list[dict[str, Any]] = field(default_factory=list)
    citations: dict[str, CitationEntry] = field(default_factory=dict)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    images: dict[str, bytes] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def issue(self, ctx: ExportContext, code: str, ref: str) -> None:
        self.diagnostics.append(
            Diagnostic(
                "error" if ctx.mode == "final" else "warning", code, f"Cannot resolve or safely render {ref}", ref
            )
        )


def spans(items: tuple[Span, ...], ctx: ExportContext, result: Resolved) -> str:
    parts = []
    for s in items:
        if isinstance(s, CiteSpan):
            entry = ctx.citation(s.citekey)
            if entry is None:
                result.issue(ctx, "unresolved_citation", s.citekey)
                parts.append(f"[unresolved: {s.citekey}]")
            else:
                result.citations[s.citekey] = entry
                parts.append(f"[{s.citekey}]")
        elif isinstance(s, MetricSpan):
            value = ctx.metric(s.run_id, s.name, s.split)
            if value is None or not math.isfinite(value.value):
                result.issue(ctx, "unresolved_metric", f"{s.run_id}/{s.name}/{s.split}")
                parts.append("[unresolved metric]")
            else:
                parts.append(
                    f"{value.value:.4f}" + (f" {value.unit}" if value.unit else "") + f" ({value.verification})"
                )
                result.metrics.append({"span": asdict(s), "resolved": asdict(value)})
        else:
            parts.append(s.text)
    return "".join(parts)


def figure(block: Block, ctx: ExportContext, result: Resolved) -> str | None:
    ident = block.artifact_id
    artifact = ctx.artifact(ident) if ident else None
    if artifact is None:
        result.issue(ctx, "missing_artifact", ident or "missing artifact id")
        return None
    if artifact.mime not in {"image/png", "image/jpeg"} or artifact.path.stat().st_size > 5_000_000:
        result.issue(ctx, "unsupported_artifact", ident or "figure")
        return None
    data = artifact.path.read_bytes()
    if hashlib.sha256(data).hexdigest() != artifact.sha256:
        result.issue(ctx, "artifact_hash_mismatch", artifact.artifact_id)
        return None
    if not (data.startswith(b"\x89PNG\r\n\x1a\n") if artifact.mime == "image/png" else data.startswith(b"\xff\xd8")):
        result.issue(ctx, "invalid_image", artifact.artifact_id)
        return None
    suffix = ".png" if artifact.mime == "image/png" else ".jpg"
    name = "figure-" + artifact.sha256 + suffix
    result.images[name] = data
    result.artifacts.append(
        {
            "artifact_id": artifact.artifact_id,
            "sha256": artifact.sha256,
            "mime": artifact.mime,
            "run_id": artifact.run_id,
            "output_name": name,
        }
    )
    return name


def resolve(snapshot: ReportSnapshot, ctx: ExportContext) -> Resolved:
    result = Resolved()
    for block in snapshot.blocks:
        item = {
            "kind": block.kind,
            "text": spans(block.spans, ctx, result),
            "level": block.level,
            "claim_id": block.claim_id,
            "caption": spans(block.caption, ctx, result),
            "rows": [[spans(cell, ctx, result) for cell in row] for row in block.rows],
        }
        if block.kind == "figure":
            try:
                item["image"] = figure(block, ctx, result)
            except OSError:
                result.issue(ctx, "missing_artifact", block.artifact_id or "figure")
        if block.kind == "table" and (not block.rows or len({len(r) for r in block.rows}) != 1):
            result.issue(ctx, "invalid_table", block.claim_id or "table")
        result.blocks.append(item)
    return result


def reference_groups(result: Resolved) -> list[tuple[list[str], CitationEntry]]:
    groups: dict[tuple[str, str], tuple[list[str], CitationEntry]] = {}
    for key, entry in result.citations.items():
        ids = dict(entry.record.identifiers)
        ident = next(
            ((scheme, ids[scheme]) for scheme in ("doi", "pmid", "arxiv") if scheme in ids),
            (entry.record.source, entry.record.source_id),
        )
        if ident in groups:
            groups[ident][0].append(key)
        else:
            groups[ident] = ([key], entry)
    return list(groups.values())


def references(result: Resolved) -> list[str]:
    return [
        "["
        + ", ".join(keys)
        + "] "
        + (", ".join(e.record.authors) + ". " if e.record.authors else "")
        + e.record.title
        + (f". {e.record.year}" if e.record.year is not None else ". Year unavailable")
        + (f". {e.record.venue}" if e.record.venue else "")
        for keys, e in reference_groups(result)
    ]


def clean(value: str) -> str:
    """XML 1.0 forbids most control characters; python-docx would raise on them (same rule as LaTeX escape)."""
    return "".join(c for c in value if ord(c) >= 32 or c in "\n\t")


def safe_output(root: Path, name: str) -> Path:
    target = root / name
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Output path escapes host directory")
    return target


SLIDE_SECTIONS = ("Background", "Objectives", "Method", "Results", "Discussion", "Conclusion")
# Common manuscript headings -> default slide section (leading section numbers are ignored).
HEADING_SECTION = {
    "background": "Background",
    "introduction": "Background",
    "related work": "Background",
    "objective": "Objectives",
    "objectives": "Objectives",
    "aim": "Objectives",
    "aims": "Objectives",
    "purpose": "Objectives",
    "research questions": "Objectives",
    "hypotheses": "Objectives",
    "method": "Method",
    "methods": "Method",
    "methodology": "Method",
    "materials and methods": "Method",
    "approach": "Method",
    "study design": "Method",
    "experimental setup": "Method",
    "result": "Results",
    "results": "Results",
    "findings": "Results",
    "evaluation": "Results",
    "discussion": "Discussion",
    "limitations": "Discussion",
    "results and discussion": "Results",
    "conclusion": "Conclusion",
    "conclusions": "Conclusion",
    "summary": "Conclusion",
    # Chinese manuscript headings (ADR-234): recognised, so zh documents get the same slide sections
    "背景": "Background",
    "引言": "Background",
    "研究背景": "Background",
    "目的": "Objectives",
    "研究目的": "Objectives",
    "方法": "Method",
    "材料与方法": "Method",
    "研究方法": "Method",
    "结果": "Results",
    "结果与讨论": "Results",
    "讨论": "Discussion",
    "局限性": "Discussion",
    "结论": "Conclusion",
    "总结": "Conclusion",
}


def slide_section(heading: str) -> str | None:
    key = re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", heading).strip().rstrip(":：").casefold()
    return HEADING_SECTION.get(key)


def slide_sections(result: Resolved) -> dict[str, list[dict[str, Any]]]:
    """Group resolved content without inventing scientific prose or resolving spans again; unrecognised headings
    stay as content of the current section."""
    sections: dict[str, list[dict[str, Any]]] = {name: [] for name in SLIDE_SECTIONS}
    current = SLIDE_SECTIONS[0]
    for block in result.blocks:
        section = slide_section(str(block["text"])) if block["kind"] == "heading" else None
        if section:
            current = section
            if block["claim_id"] or block["caption"]:
                sections[current].append({**block, "text": ""})
        else:
            sections[current].append(block)
    return sections
