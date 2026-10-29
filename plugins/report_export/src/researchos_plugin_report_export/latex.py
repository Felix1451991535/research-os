"""Escaped Unicode LaTeX and BibTeX; never invokes an unmanaged process."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from researchos.contracts.exporters import ReportSnapshot
from researchos.sdk.texts import export_language

from .resolve import Resolved, reference_groups, references
from .texts import TEXTS

ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "%": r"\%",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape(value: str) -> str:
    return "".join(ESCAPES.get(c, c) for c in value if ord(c) >= 32 or c in "\n\t")


def bibtex(result: Resolved) -> str:
    entries = []
    for keys, entry in reference_groups(result):
        key = keys[0]
        record = entry.record
        safe = "ref" + hashlib.sha256(key.encode()).hexdigest()[:24]
        fields = {
            "title": record.title,
            "author": " and ".join(record.authors) or None,
            "year": str(record.year) if record.year is not None else None,
            "journal": record.venue,
            "doi": dict(record.identifiers).get("doi"),
            "url": record.url,
        }
        entries.append(
            "@misc{"
            + safe
            + ",\n"
            + ",\n".join(f"  {k} = {{{escape(v)}}}" for k, v in fields.items() if v is not None)
            + "\n}"
        )
    return "\n\n".join(entries) + "\n"


def render(snapshot: ReportSnapshot, result: Resolved, template: str, options: Mapping[str, Any]) -> str:
    T = TEXTS.lang(export_language(options))
    style = (
        r"\documentclass[10pt,twocolumn]{article}" if template == "short_paper" else r"\documentclass[11pt]{article}"
    )
    lines = [
        style,
        r"\usepackage{fontspec}",
        r"\usepackage{graphicx}",
        r"\usepackage{longtable}",
        r"\usepackage{xeCJK}",
        r"\setmainfont{" + escape(str(options.get("english_font", "Times New Roman"))) + "}",
        r"\setCJKmainfont{" + escape(str(options.get("chinese_font", "SimSun"))) + "}",
        r"\usepackage[margin=" + str(options.get("margin_mm", 25)) + "mm]{geometry}",
        r"\title{" + escape(snapshot.title) + "}",
        r"\date{}",
        r"\begin{document}",
        r"\maketitle",
        r"\fontsize{" + str(options.get("font_size", 11)) + r"}{14}\selectfont",
    ]
    if template == "literature_review":
        lines.extend([r"\setlength{\parskip}{6pt}", r"\setlength{\parindent}{0pt}"])
    for block in result.blocks:
        value = escape(str(block["text"]))
        if block["kind"] == "heading":
            command = "section" if int(block["level"]) <= 1 else "subsection"
            lines.append("\\" + command + "*{" + value + "}")
        elif block["kind"] == "table" and block["rows"]:
            lines.extend(
                [r"\begin{tabular}{" + "l" * len(block["rows"][0]) + "}"]
                + [" & ".join(escape(c) for c in row) + r" \\" for row in block["rows"]]
                + [r"\end{tabular}"]
            )
        elif block["kind"] == "figure" and block.get("image"):
            lines.append(r"\includegraphics[width=\linewidth]{" + str(block["image"]) + "}")
        else:
            lines.append(value + "\n")
        if block["claim_id"]:
            lines.append(r"\par\textit{" + escape(T("claim", id=block["claim_id"])) + "}")
        if block["caption"]:
            lines.append(r"\par " + escape(str(block["caption"])))
    lines += [r"\section*{" + escape(T("references")) + "}"] + [escape(ref) + "\n" for ref in references(result)]
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"
