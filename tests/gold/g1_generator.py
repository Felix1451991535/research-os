"""G1 synthetic gold set (ARCHITECTURE §5.4): PDFs generated with KNOWN text, so anchor ground truth comes from
the generator, not from a model. Cases: single column with headings + DOI, two-column page, rotated page,
scanned (image-only) page, Chinese text, corrupt bytes, encrypted, exact duplicate."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

W, H = A4


@dataclass
class GoldDoc:
    name: str
    data: bytes
    expect_state: str  # parsed | partial | needs_ocr | failed | unsupported
    anchors: list[tuple[int, str]] = field(default_factory=list)  # (page_index, exact sentence on that page)
    expect_error: str | None = None


SENTENCES_P1 = [
    "Human activity recognition uses inertial sensors worn on the body.",
    "The UCI HAR dataset contains recordings from 30 subjects performing 6 activities.",
    "Signals were sampled at 50 Hz and segmented into windows of 2.56 seconds.",
]
SENTENCES_P2 = [
    "A random forest baseline reached a macro F1 score of 0.91 on the held-out subjects.",
    "The convolutional model used 31,000 parameters and ran in 1.8 ms per window on the CPU.",
]


def _lines(
    c: canvas.Canvas, lines: list[str], x: float = 72, y: float = H - 90, size: int = 11, gap: int = 16
) -> float:
    c.setFont("Helvetica", size)
    for line in lines:
        c.drawString(x, y, line)
        y -= gap
    return y


def single_column() -> GoldDoc:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Synthetic HAR study for anchor testing")
    y = _lines(c, ["Synthetic HAR study for anchor testing", "doi:10.5555/ros.g1.0001", "", "Abstract"], size=12)
    y = _lines(c, SENTENCES_P1, y=y - 6)
    _lines(c, ["", "1. Introduction", "Wearable sensing is common in clinical monitoring research."], y=y - 6)
    c.showPage()
    y = _lines(c, ["2. Results"] + SENTENCES_P2, size=11)
    _lines(c, ["", "References", "[1] Anguita D. et al. A public domain dataset for HAR. 2013."], y=y - 6)
    c.showPage()
    c.save()
    return GoldDoc(
        "single_column.pdf", buf.getvalue(), "parsed", [(0, s) for s in SENTENCES_P1] + [(1, s) for s in SENTENCES_P2]
    )


def two_column() -> GoldDoc:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    left = [f"Left column sentence number {i} about sensor placement." for i in range(1, 16)]
    right = [f"Right column sentence number {i} about model evaluation." for i in range(1, 16)]
    c.setFont("Helvetica", 9)
    for i, (a, b) in enumerate(zip(left, right, strict=True)):
        c.drawString(50, H - 80 - i * 14, a)
        c.drawString(W / 2 + 15, H - 80 - i * 14, b)
    c.showPage()
    c.save()
    return GoldDoc("two_column.pdf", buf.getvalue(), "partial", [(0, left[3]), (0, right[7])])


def rotated() -> GoldDoc:
    base = single_column().data
    w = PdfWriter()
    for i, p in enumerate(PdfReader(io.BytesIO(base)).pages):
        if i == 1:
            p.rotate(90)
        w.add_page(p)
    out = io.BytesIO()
    w.write(out)
    return GoldDoc("rotated.pdf", out.getvalue(), "parsed", [(1, SENTENCES_P2[0])])


def scanned() -> GoldDoc:
    img = Image.new("RGB", (1240, 1754), "white")
    ImageDraw.Draw(img).text((100, 100), "This page is an image only scan.", fill="black")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawImage(ImageReader(img), 0, 0, W, H)
    c.showPage()
    c.save()
    return GoldDoc("scanned.pdf", buf.getvalue(), "needs_ocr")


CHINESE = "惯性传感器用于人体活动识别研究。受试者佩戴智能手机采集加速度数据。"


def chinese() -> GoldDoc:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("STSong-Light", 12)
    c.drawString(72, H - 90, "人体活动识别综述")
    for i, s in enumerate(CHINESE.split("。")[:2]):
        c.drawString(72, H - 120 - i * 18, s + "。")
    c.drawString(72, H - 180, "This bilingual page also has English text for robustness checks.")
    c.showPage()
    c.save()
    return GoldDoc(
        "chinese.pdf",
        buf.getvalue(),
        "parsed",
        [(0, "惯性传感器用于人体活动识别研究。"), (0, "受试者佩戴智能手机采集加速度数据。")],
    )


def corrupt() -> GoldDoc:
    data = single_column().data
    return GoldDoc("corrupt.pdf", data[:400] + b"\x00garbage" * 20, "failed", expect_error="corrupt")


def encrypted() -> GoldDoc:
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(single_column().data)))
    w.encrypt(user_password="secret-pass", owner_password="owner-pass")
    out = io.BytesIO()
    w.write(out)
    return GoldDoc("encrypted.pdf", out.getvalue(), "failed", expect_error="encrypted")


def all_docs() -> list[GoldDoc]:
    docs = [single_column(), two_column(), rotated(), scanned(), chinese(), corrupt(), encrypted()]
    docs.append(GoldDoc("duplicate_of_single.pdf", docs[0].data, "parsed"))
    return docs
