"""DOCX, LaTeX and slides exporters driven solely by structured spans and host resolvers."""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any, cast

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt
from docx.styles.style import ParagraphStyle
from researchos.contracts.exporters import (
    Diagnostic,
    ExportContext,
    ExporterSpec,
    ExportFile,
    ExportResult,
    ReportSnapshot,
)

from .latex import bibtex, render
from .resolve import Resolved, clean, references, resolve, safe_output

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
OPTIONS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "template": {"enum": ["lab_report", "literature_review", "short_paper"], "default": "lab_report"},
        "english_font": {"type": "string", "default": "Times New Roman"},
        "chinese_font": {"type": "string", "default": "SimSun"},
        "font_size": {"type": "number", "minimum": 8, "maximum": 24, "default": 11},
        "pdf": {"type": "boolean", "default": False},
        "margin_mm": {"type": "number", "minimum": 10, "maximum": 50, "default": 25},
    },
    "additionalProperties": False,
}


def validate_options(options: Mapping[str, Any], schema: Mapping[str, Any] = OPTIONS) -> str:
    import jsonschema

    validate = cast(Callable[[object, object], None], jsonschema.validate)
    validate(dict(options), schema)
    return str(options.get("template", "lab_report"))


def provenance(snapshot: ReportSnapshot, result: Resolved, ctx: ExportContext) -> ExportFile:
    payload = {
        "snapshot": asdict(snapshot),
        "citations": {k: asdict(v) for k, v in result.citations.items()},
        "metrics": result.metrics,
        "artifacts": result.artifacts,
        "diagnostics": [asdict(d) for d in result.diagnostics],
    }
    safe_output(ctx.out_dir, "provenance.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return ExportFile("provenance.json", "application/json")


def docx_bytes(snapshot: ReportSnapshot, result: Resolved, options: Mapping[str, Any], template: str) -> bytes:
    doc = Document()
    configure_docx(doc, options, template)
    doc.add_heading(clean(snapshot.title), 0)
    for block in result.blocks:
        kind, value = block["kind"], clean(str(block["text"]))
        if kind == "heading":
            doc.add_heading(value, min(9, max(1, int(block["level"]))))
        elif kind == "table" and block["rows"]:
            table = doc.add_table(rows=0, cols=len(block["rows"][0]))
            table.style = "Table Grid"
            for row in block["rows"]:
                cells = table.add_row().cells
                for cell, cell_value in zip(cells, row, strict=True):
                    cell.text = clean(cell_value)
        elif kind == "figure" and block.get("image"):
            doc.add_picture(
                io.BytesIO(result.images[block["image"]]), width=Inches(3 if template == "short_paper" else 5)
            )
        else:
            doc.add_paragraph(value, style="List Bullet" if kind == "list" else None)
        if block["claim_id"]:
            doc.add_paragraph("Claim: " + clean(str(block["claim_id"])))
        if block["caption"]:
            doc.add_paragraph(clean(str(block["caption"])), style="Caption")
    doc.add_heading("References", 1)
    for reference in references(result):
        doc.add_paragraph(clean(reference))
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


class DocxExporter:
    spec = ExporterSpec(
        "report_docx",
        "0.1.0",
        ("report", "answer", "manuscript"),
        (DOCX, "application/json"),
        "DOCX with evidence provenance sidecar",
        OPTIONS,
    )

    def export(self, snapshot: ReportSnapshot, ctx: ExportContext, options: Mapping[str, Any]) -> ExportResult:
        template = validate_options(options)
        result = resolve(snapshot, ctx)
        if any(d.severity == "error" for d in result.diagnostics):
            return ExportResult((), tuple(result.diagnostics))
        data = docx_bytes(snapshot, result, options, template)
        safe_output(ctx.out_dir, "report.docx").write_bytes(data)
        sidecar = provenance(snapshot, result, ctx)
        if options.get("pdf"):
            result.diagnostics.append(
                Diagnostic("warning", "pdf_unavailable", "No public managed PDF process contract")
            )
        return ExportResult((ExportFile("report.docx", DOCX), sidecar), tuple(result.diagnostics))


class LatexExporter:
    spec = ExporterSpec(
        "report_latex",
        "0.1.0",
        ("report", "answer", "manuscript"),
        ("application/x-tex", "application/x-bibtex", "application/json", "image/png", "image/jpeg"),
        "Escaped LaTeX, BibTeX and evidence sidecar; compile externally",
        OPTIONS,
    )

    def export(self, snapshot: ReportSnapshot, ctx: ExportContext, options: Mapping[str, Any]) -> ExportResult:
        template = validate_options(options)
        result = resolve(snapshot, ctx)
        if any(d.severity == "error" for d in result.diagnostics):
            return ExportResult((), tuple(result.diagnostics))
        safe_output(ctx.out_dir, "report.tex").write_text(render(snapshot, result, template, options), encoding="utf-8")
        safe_output(ctx.out_dir, "references.bib").write_text(bibtex(result), encoding="utf-8")
        files = [ExportFile("report.tex", "application/x-tex"), ExportFile("references.bib", "application/x-bibtex")]
        for name, data in result.images.items():
            safe_output(ctx.out_dir, name).write_bytes(data)
            files.append(ExportFile(name, "image/png" if name.endswith(".png") else "image/jpeg"))
        if options.get("pdf"):
            result.diagnostics.append(
                Diagnostic("warning", "pdf_unavailable", "No public managed LaTeX process contract")
            )
        files.append(provenance(snapshot, result, ctx))
        return ExportResult(tuple(files), tuple(result.diagnostics))


def configure_docx(doc: DocxDocument, options: Mapping[str, Any], template: str) -> None:
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Mm(float(options.get("margin_mm", 25)))
        section.left_margin = section.right_margin = Mm(float(options.get("margin_mm", 25)))
    style = cast(ParagraphStyle, doc.styles["Normal"])
    style.paragraph_format.space_after = Pt(8 if template == "literature_review" else 4)
    style.font.name = str(options.get("english_font", "Times New Roman"))
    style.font.size = Pt(float(options.get("font_size", 10 if template == "short_paper" else 11)))
    fonts = OxmlElement("w:rFonts")
    fonts.set(qn("w:ascii"), str(options.get("english_font", "Times New Roman")))
    fonts.set(qn("w:hAnsi"), str(options.get("english_font", "Times New Roman")))
    fonts.set(qn("w:eastAsia"), str(options.get("chinese_font", "SimSun")))
    style.element.get_or_add_rPr().append(fonts)
    if template == "short_paper":
        cols = doc.sections[0]._sectPr.find(qn("w:cols"))
        if cols is not None:
            cols.set(qn("w:num"), "2")
