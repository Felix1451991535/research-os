"""Generated 30-PDF library fixture (SYNTHETIC; reportlab): 20 DOI papers, 2 byte-identical duplicates, 2 other
versions of existing DOIs, 3 arXiv preprints, 2 PDFs without identifiers (one named CON.pdf), 1 corrupt PDF,
plus one non-PDF file. Known truth for the organisation plan."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .m5_helpers import rec


def _pdf(path: Path, lines: list[str]) -> None:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), invariant=1)
    y = 800
    for line in lines:
        c.drawString(60, y, line)
        y -= 18
    c.showPage()
    c.save()


def build(root: Path) -> dict[str, object]:
    src = root / "Downloads"
    (src / "sub").mkdir(parents=True)
    hits: dict[str, list[object]] = {}
    expect: dict[str, str] = {}
    for i in range(1, 21):
        doi = f"10.5555/lib.{i}"
        p = src / f"paper{i:02d}.pdf"
        _pdf(p, [f"Synthetic study number {i} on signals", f"https://doi.org/{doi}", "Body text."])
        year = 2015 + i % 5
        hits[doi] = [rec("fake_lib", f"d{i}", f"Signals study {i}", doi, year=year)]
        hits[doi][0] = hits[doi][0].__class__(**{**hits[doi][0].__dict__, "authors": (f"Ann Smith{chr(96 + i)}",)})
        expect[p.name] = f"{year}/smith{chr(96 + i)}{year}signals.pdf"
    shutil.copyfile(src / "paper01.pdf", src / "sub" / "copy_of_paper01.pdf")      # byte duplicate
    shutil.copyfile(src / "paper02.pdf", src / "sub" / "paper02 (1).pdf")           # byte duplicate
    for i in (3, 4):                                                                # other version, same DOI
        p = src / "sub" / f"paper{i:02d}_accepted.pdf"
        _pdf(p, [f"Accepted manuscript {i}", f"doi:10.5555/lib.{i}"])
        y = 2015 + i % 5
        expect[p.name] = f"{y}/smith{chr(96 + i)}{y}signals-v2.pdf"
    for j in range(1, 4):
        aid = f"2401.0000{j}"
        p = src / f"preprint{j}.pdf"
        _pdf(p, ["A preprint", f"arXiv:{aid}v2 [cs.LG]"])
        from researchos.contracts.datasources import BibRecord

        hits[aid] = [BibRecord("fake_lib", f"a{j}", f"Learning things {j}", (f"Bo Liang{j}",), 2024,
                               identifiers=(("arxiv", aid),), kind="preprint")]
        expect[p.name] = f"2024/liang{j}2024learning.pdf"
    _pdf(src / "notes from talk.pdf", ["Slides without any identifier"])
    expect["notes from talk.pdf"] = "_unsorted/notes from talk.pdf"
    _pdf(src / "CON.pdf", ["Reserved Windows device name"])
    expect["CON.pdf"] = "_unsorted/_CON.pdf"
    (src / "broken.pdf").write_bytes(b"%PDF-1.4\n" + hashlib.sha256(b"x").digest() * 50)
    expect["broken.pdf"] = "_unsorted/broken.pdf"
    (src / "readme.txt").write_text("not a pdf")
    return {"src": src, "hits": hits, "expect": expect}


def tree(root: Path) -> dict[str, str]:
    """Relative path -> sha256 (files) or 'dir'; the equality used by the undo gate."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()
    return out
