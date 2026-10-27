"""M6 number gate: which prose tokens are numbers that need a source (review #3, #15-#17)."""

from __future__ import annotations

import sqlite3


def test_embedded_quantities_are_free_numbers(conn):
    from researchos.services.writing.doc_snapshot import parse
    from researchos.services.writing.gates import check

    result = check(conn, parse("n30 participants and top5 accuracy; GPT-4 and ResNet-50."), "final")
    assert sorted(d.ref for d in result.diagnostics if d.code == "free_number") == ["30", "5"]

    result = check(conn, parse("F1, R2 and AUC."), "final")
    assert not result.errors
    assert result.counts["free_numbers"] == 0

    result = check(conn, parse("top-5 accuracy, n-30 participants and a change of -5 or +5."), "final")
    assert [d.ref for d in result.diagnostics if d.code == "free_number"] == ["5", "30", "-5", "+5"]


def test_leading_dot_decimals_are_free_numbers(conn: sqlite3.Connection) -> None:
    """Regression (review #15): ".05", "-.05" and ".95%" are numbers; ".05" equals a quoted "0.05"."""
    from researchos.services.knowledge.text import numbers_in
    from researchos.services.writing.doc_snapshot import parse
    from researchos.services.writing.gates import check

    result = check(conn, parse("p = .05; accuracy = .95; change -.05 and .95% overall."), "final")
    assert [d.ref for d in result.diagnostics if d.code == "free_number"] == [".05", ".95", "-.05", ".95%"]
    assert numbers_in("p = .05") == numbers_in("p = 0.05") == ["0.05"]


def _evidence(conn: sqlite3.Connection, quote: str) -> str:
    """A real, verified Evidence row quoting `quote` (minimal work + version + page)."""
    import hashlib

    from researchos.kernel.db import utcnow
    from researchos.services.knowledge import evidence

    now, wid = utcnow(), "work_" + hashlib.sha256(quote.encode()).hexdigest()[:8]
    conn.execute("INSERT INTO works(id,title,created_at,updated_at) VALUES (?,?,?,?)", (wid, "W", now, now))
    vid = "ver_" + wid
    conn.execute(
        "INSERT INTO document_versions(version_id,work_id,sha256,stored_path,original_name,size,mime,"
        "parse_state,parser_version,created_at) VALUES (?,?,?,'raw/x.pdf','x.pdf',1,'application/pdf',"
        "'parsed','p',?)",
        (vid, wid, hashlib.sha256(vid.encode()).hexdigest(), now),
    )
    conn.execute(
        "INSERT INTO pages(version_id,page_index,text,text_sha256,char_count) VALUES (?,0,?,?,?)",
        (vid, quote, hashlib.sha256(quote.encode()).hexdigest(), len(quote)),
    )
    conn.commit()
    return str(evidence.create(conn, version_id=vid, page_index=0, quote=quote, created_by="test")["id"])


def test_scientific_notation_is_one_quantity(conn: sqlite3.Connection) -> None:
    """Regression (review #16): "1e-3" cannot borrow the digits 1 and 3 from unrelated evidence; equal
    quantities in different notations do match."""
    from researchos.services.writing.doc_snapshot import parse
    from researchos.services.writing.gates import check

    unrelated = _evidence(conn, "We conducted 1 trial with 3 subjects.")
    res = check(conn, parse(f"The error rate was 1e-3 {{{{evidence:{unrelated}}}}}."), "final")
    assert [d.ref for d in res.diagnostics if d.code == "free_number"] == ["1e-3"]
    quoted = _evidence(conn, "The error rate was 1e-3 overall.")
    for prose in ("1×10^-3", "1 × 10⁻³", "1E-3"):
        res = check(conn, parse(f"Errors were {prose} {{{{evidence:{quoted}}}}}."), "final")
        assert not res.errors, (prose, res.diagnostics)


def test_unicode_minus_is_a_sign(conn: sqlite3.Connection) -> None:
    """Regression (review #17): evidence quoting +5 cannot source a claimed −5 (U+2212); a quoted −5 can."""
    from researchos.services.writing.doc_snapshot import parse
    from researchos.services.writing.gates import check

    positive = _evidence(conn, "The measured change was 5 points.")
    res = check(conn, parse(f"The change was −5 points {{{{evidence:{positive}}}}}."), "final")
    assert [d.ref for d in res.diagnostics if d.code == "free_number"] == ["−5"]
    negative = _evidence(conn, "The measured change was −5 points.")
    for prose in ("−5", "-5"):
        assert not check(conn, parse(f"It fell by {prose} {{{{evidence:{negative}}}}}."), "final").errors


def test_scientific_notation_with_unit_is_one_quantity(conn: sqlite3.Connection) -> None:
    from researchos.services.knowledge.text import missing_numbers, numbers_in
    from researchos.services.writing.doc_snapshot import parse
    from researchos.services.writing.gates import check

    unrelated = _evidence(conn, "We conducted 1 trial with 3 subjects.")
    quoted = _evidence(conn, "The latency was 1e-3ms.")
    for value in ("1e-3ms", "1E-3ms", "1e−3ms", "1×10^-3ms", "1×10⁻³ms"):
        assert numbers_in(value) == ["1e-3"]
        assert missing_numbers(value, "1 trial with 3 subjects") == ["1e-3"]
        res = check(conn, parse(f"Latency {value} {{{{evidence:{unrelated}}}}}."), "final")
        assert len([d for d in res.diagnostics if d.code == "free_number"]) == 1
        assert not check(conn, parse(f"Latency {value} {{{{evidence:{quoted}}}}}."), "final").errors


def test_unicode_minus_thousands_normalization() -> None:
    from researchos.services.knowledge.text import numbers_in

    for sign in ("−", "﹣", "－"):
        assert numbers_in(f"{sign}1,000") == ["-1000"]
        assert numbers_in(f"{sign}1,000.5%") == ["-1000.5%"]
