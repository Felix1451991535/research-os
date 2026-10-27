"""Deterministic text normalization used by the evidence write path and citation checks.

normalize() keeps a map from every normalized character back to its original offset, so a quote found in the
normalized page text can be anchored to exact original char offsets.
"""
from __future__ import annotations

import re
import unicodedata

_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "­": ""}
_DASHES = {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"}
_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"'}
# Number syntax shared by the evidence scanner (NUMBER_RE) and the writing gate's prose scanner, so both read
# numbers the same way. A leading-dot decimal (".05") is one number (review #15).
# Signs include the Unicode minus (U+2212) and its small/full-width forms, normalized to "-" by canon_number, so a
# quoted "−5" is never read as +5 (review #17). En dash is not a minus ("3–5" is a range).
_MINUS = "\u2212\ufe63\uff0d"
SIGN = rf"[-+{_MINUS}]"
INT_PART = r"\d+(?:[.,]\d+)*"
DOT_PART = r"\.\d+"
PERCENT = r"(?:\s?%)?"
# Scientific notation is ONE number ("1e-3", "1.2E5", "1×10^-3", "1×10⁻³"), never digits to match one by one
# (review #16, #18), including an attached unit ("1e-3ms").
_SUP = "⁰¹²³⁴⁵⁶⁷⁸⁹"
EXPONENT = rf"(?:[eE]{SIGN}?\d+|\s?[×x]\s?10(?:\^{SIGN}?\d+|[⁻⁺]?[{_SUP}]+))?"
NUMBER_RE = re.compile(rf"(?<![\w.]){SIGN}?(?:{INT_PART}|{DOT_PART}){EXPONENT}{PERCENT}")

# Numbers that are labels, not data (ADR-119). Structural references take any number; study-design labels only a
# capitalised label with 1-2 digits ("Study 2", not "steps 1000"); years only in citation-like contexts ("(2019)",
# "et al., 2019", "in 2019"), never a bare 1800-2100 integer ("N = 2000"); model/dataset names only in upper-case or
# CamelCase form ("GPT-4", "COVID-19", "ResNet-50"), never lower-case ("top-5", "n-30").
# Conventional metric names (F1, R2, AUC) are labels too, not measured values.
_YEAR = r"(?:1[89]\d\d|20\d\d|2100)[a-z]?"
EXEMPT = re.compile(
    r"(?i:\b(?:fig(?:ure)?s?\.?|tables?|sections?|sec\.|eqs?\.?|equations?|chapters?|appendix|"
    r"hypothes[ie]s|rq|refs?\.?|pp?\.)\s*\d+(?:[.\-–]\d+)*[a-z]?)"
    r"|\b(?:Study|Studies|Experiments?|Phases?|Stages?|Rounds?|Steps?|Trials?|Waves?)\s+\d{1,2}[a-z]?\b"
    rf"|(?<=\()\s*{_YEAR}(?=\s*[),;])|(?i:\bet al\.,?\s*{_YEAR}\b)|,\s*{_YEAR}(?=\s*[);])"
    rf"|(?i:\b(?:in|since|until|during)\s+{_YEAR}(?:\s*[\-–]\s*{_YEAR})?\b)"
    r"|\b(?:F1|R2|AUC)\b"
    r"|\b(?:[A-Z]{2,}|[A-Z][a-z]+[A-Z][A-Za-z]*)[\-_]?\d+[A-Za-z\d]*\b")
# Prose scanner (stricter than NUMBER_RE): also finds numbers embedded after letters ("n30", "top5");
# a sign is unary only when it does not follow a word character ("top-5" is 5, not -5).
PROSE_NUMBER_RE = re.compile(
    rf"(?<![\d.])(?:(?<!\w){SIGN})?(?:{INT_PART}|(?<!\w){DOT_PART}){EXPONENT}{PERCENT}"
)


def prose_numbers_missing(text: str, quote: str) -> list[str]:
    """Numbers the export gate would flag in `text` when `quote` is its only source (ADR-198): label numbers are
    exempt, and digits glued to letters count ("colin27" needs "27" in the quote). The cited-QA validator uses this,
    so every kept sentence also passes the final export gate."""
    sourced = {canon_number(m.group(0)) for m in PROSE_NUMBER_RE.finditer(quote)}
    found = PROSE_NUMBER_RE.finditer(EXEMPT.sub(" ", text))
    return [m.group(0) for m in found if canon_number(m.group(0)) not in sourced]


def _char_map(c: str) -> str:
    if c in _LIGATURES:
        return _LIGATURES[c]
    c = _DASHES.get(c, _QUOTES.get(c, c))
    return unicodedata.normalize("NFKC", c)


def normalize(text: str) -> tuple[str, list[int]]:
    """Returns (normalized_text, index_map) where index_map[i] = original offset of normalized char i.
    Rules: NFKC + ligatures/dashes/quotes unified, line-break hyphenation removed ("exam-\\nple" -> "example"),
    any whitespace run -> one space, case folded."""
    out: list[str] = []
    idx: list[int] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "-" and i + 1 < n and text[i + 1] == "\n" and out and out[-1].isalpha():
            i += 2  # join hyphenated line break
            while i < n and text[i] in " \t":
                i += 1
            continue
        if c.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                idx.append(i)
            i += 1
            continue
        for m in _char_map(c).casefold():
            out.append(m)
            idx.append(i)
        i += 1
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def norm(text: str) -> str:
    return normalize(text)[0].strip()


def find_span(page_text: str, quote: str) -> tuple[int, int] | None:
    """Locate quote in page_text after normalization; returns ORIGINAL (start, end) offsets or None."""
    nq = norm(quote)
    if not nq:
        return None
    npage, idx = normalize(page_text)
    pos = npage.find(nq)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(nq) - 1] + 1


def numbers_in(text: str) -> list[str]:
    return [canon_number(m.group(0)) for m in NUMBER_RE.finditer(text)]


def canon_number(s: str) -> str:
    s = s.replace(" ", "").translate({ord(c): "-" for c in _MINUS}).lstrip("+")
    s = s.replace(" ", "").replace(",", "") if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+(\.\d+)?%?", s.strip()) else s
    s = re.sub(r"^(-?)\.", r"\g<1>0.", s)  # ".05" == "0.05"
    m = re.fullmatch(r"(.+?)(?:[eE]|[×x]10\^?)([-+⁻⁺]?)([\d⁰¹²³⁴⁵⁶⁷⁸⁹]+)(%?)", s)
    if m:  # one canonical exponent form: "1E-3", "1×10^-3", "1×10⁻³" -> "1e-3"
        sign = "-" if m.group(2) in ("-", "⁻") else ""
        exp = m.group(3).translate(str.maketrans(_SUP, "0123456789")).lstrip("0") or "0"
        s = f"{m.group(1)}e{sign}{exp}{m.group(4)}"
    return s


def missing_numbers(value: str, quote: str) -> list[str]:
    """Numbers that appear in an extracted value but not in its supporting quote (quote-or-reject rule)."""
    have = set(numbers_in(quote))
    return [x for x in numbers_in(value) if x not in have]
