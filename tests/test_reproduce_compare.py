"""Feature 5: service comparison over real evidence and the K9 ingestion path."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from researchos.contracts.errors import ApiError
from researchos.services.container import Services
from researchos.services.execution import compare as comparison_svc
from researchos.services.execution import ingest, plans
from researchos.services.knowledge import evidence
from researchos.services.orchestration import tasks, workers
from researchos.services.verification.view import DirRunView


@pytest.fixture
def comparison(conn: sqlite3.Connection, svc: Services, tmp_path: Path) -> dict[str, Any]:
    quote = "Accuracy was 80 %. Batch size was 32. Latency was 1,000 ms."
    conn.execute("INSERT INTO works(id,title,created_at,updated_at) VALUES ('paper','Paper','now','now')")
    conn.execute(
        "INSERT INTO document_versions(version_id,work_id,sha256,stored_path,original_name,size,mime,"
        "parser_version,created_at) VALUES ('version','paper','hash','paper.pdf','paper.pdf',1,"
        "'application/pdf','test','now')"
    )
    conn.execute(
        "INSERT INTO pages(version_id,page_index,text,text_sha256,char_count) VALUES ('version',0,?,?,?)",
        (quote, hashlib.sha256(quote.encode()).hexdigest(), len(quote)),
    )
    ev = evidence.create(conn, version_id="version", page_index=0, quote=quote, created_by="test")
    rev = plans.create(
        conn,
        title="reproduce",
        actor="test",
        content={
            "hypothesis": "reproduce paper",
            "template": {"id": "fixture", "hash": "h"},
            "metrics": ["accuracy"],
            "limits": {},
            "params": {"batch": 64},
        },
    )
    task, _ = tasks.create(
        conn,
        svc.kinds,
        kind="experiment.run",
        idempotency_key="comparison",
        actor="test",
        payload={"plan_id": rev["plan_id"], "revision": 1, "stage": "trial", "params": {"batch": 64}, "seed": 0},
    )
    worker = workers.register(conn, 0, "test")
    conn.execute(
        "INSERT INTO runs(id,task_id,attempt,lease_generation,worker_id,trace_id,state,started_at) "
        "VALUES ('run',?,1,1,?,'trace','succeeded','now')",
        (task["id"], worker),
    )
    run_dir = tmp_path / "run"
    (run_dir / "predictions").mkdir(parents=True)
    (run_dir / "predictions" / "test.csv").write_text("y_true,y_pred\n" + "1,1\n" * 4 + "1,0\n")
    (run_dir / "summary.json").write_text(
        json.dumps([{"name": "accuracy", "split": "test", "value": 0.8, "verification": "recomputed"}])
    )
    ingest.ingest(conn, tmp_path, "run", DirRunView(run_dir, {}), [])
    return {
        "evidence_id": ev["id"],
        "number_index": 0,
        "metric": "accuracy",
        "split": "test",
        "unit": "%",
        "tolerance": {"kind": "absolute", "value": 0.01},
    }


def compare(conn: sqlite3.Connection, item: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return comparison_svc.compare_run(conn, "run", [item], **kw)["items"][0]


def test_within_tolerance(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    r = compare(conn, comparison)
    assert r["status"] == "match"
    assert r["reported_value"] == r["reproduced_value"] == 0.8
    assert r["evidence_ref"] == "{{evidence:" + comparison["evidence_id"] + "}}"
    assert r["metric_ref"] == "{{metric:run/accuracy/test}}"
    assert r["candidate_causes"] == []


def test_deviation_candidate_params(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    conn.execute("UPDATE run_metrics SET value=0.6")
    r = compare(
        conn, comparison, paper_params=[{"name": "batch", "evidence_id": comparison["evidence_id"], "number_index": 1}]
    )
    assert r["status"] == "deviation"
    cause = r["candidate_causes"][0]
    assert cause["label"] == "candidate cause"
    assert (cause["paper_value"], cause["plan_value"], cause["run_value"]) == (32, 64, 64)
    assert cause["quote"]


@pytest.mark.parametrize("damage", ["stale", "unverified", "failed", "missing", "hypothesis", "superseded"])
def test_evidence_not_comparable(conn: sqlite3.Connection, comparison: dict[str, Any], damage: str) -> None:
    if damage == "stale":
        conn.execute("UPDATE pages SET text='changed'")
    elif damage in ("unverified", "failed"):
        conn.execute("UPDATE evidence SET trust=?", (damage,))
    elif damage == "hypothesis":
        conn.execute("UPDATE evidence SET kind='hypothesis'")
    elif damage == "superseded":
        conn.execute(
            "INSERT INTO document_versions(version_id,work_id,sha256,stored_path,original_name,size,mime,"
            "created_at) VALUES ('new','paper','new','x','x',1,'application/pdf','zzz')"
        )
    else:
        comparison["evidence_id"] = "missing"
    assert compare(conn, comparison)["status"] == "not comparable"


@pytest.mark.parametrize("damage", ["missing", "failed", "self_reported", "unverified", "null", "run_failed"])
def test_metric_not_comparable(conn: sqlite3.Connection, comparison: dict[str, Any], damage: str) -> None:
    if damage == "missing":
        conn.execute("DELETE FROM run_metrics")
    elif damage == "null":
        conn.execute("UPDATE run_metrics SET value=NULL")
    elif damage == "run_failed":
        conn.execute("UPDATE runs SET state='failed'")
    else:
        conn.execute("UPDATE run_metrics SET verification=?", (damage,))
    assert compare(conn, comparison)["status"] == "not comparable"


def test_units_and_relative_tolerance(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    comparison.update(number_index=2, unit="ms", tolerance={"kind": "relative", "value": 0.01})
    conn.execute("UPDATE run_metrics SET value=1005, unit='ms'")
    r = compare(conn, comparison)
    assert r["status"] == "match" and r["reported_value"] == 1000 and r["threshold"] == 10
    conn.execute("UPDATE run_metrics SET unit='s'")
    assert compare(conn, comparison)["status"] == "not comparable"


def test_bad_selection_and_tolerance(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    comparison["number_index"] = 99
    assert compare(conn, comparison)["status"] == "not comparable"
    comparison["tolerance"]["value"] = -1
    with pytest.raises(ApiError):
        compare(conn, comparison)


def test_route_delegates(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    # Direct route invocation avoids the sandbox's hanging synchronous HTTP fixture.
    from researchos.api.routers.experiments import CompareIn, compare_run

    assert compare_run("run", CompareIn(items=[comparison]), conn=conn, p=None)["items"][0]["status"] == "match"


def test_table_keeps_missing_items(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    result = comparison_svc.compare_run(conn, "run", [comparison, {**comparison, "metric": "missing"}])
    assert [r["status"] for r in result["items"]] == ["match", "not comparable"]


@pytest.mark.parametrize("kind", ["absolute", "relative"])
def test_tolerance_boundary(conn: sqlite3.Connection, comparison: dict[str, Any], kind: str) -> None:
    conn.execute("UPDATE run_metrics SET value=0.81")
    comparison["tolerance"] = {"kind": kind, "value": 0.01 if kind == "absolute" else 0.0125}
    assert compare(conn, comparison)["status"] == "match"
    conn.execute("UPDATE run_metrics SET value=0.810001")
    assert compare(conn, comparison)["status"] == "deviation"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1])
def test_invalid_tolerance(conn: sqlite3.Connection, comparison: dict[str, Any], value: float) -> None:
    comparison["tolerance"]["value"] = value
    with pytest.raises(ApiError):
        compare(conn, comparison)


def test_percent_metric_and_human_checked_source(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    conn.execute("UPDATE evidence SET trust='human_checked'")
    conn.execute("UPDATE run_metrics SET value=80, unit='%'")
    assert compare(conn, comparison)["status"] == "match"


def test_unverified_parameter_is_not_a_candidate(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    conn.execute("UPDATE run_metrics SET value=0.6")
    r = compare(conn, comparison, paper_params=[{"name": "batch", "evidence_id": "missing", "number_index": 0}])
    assert r["status"] == "deviation" and not r["candidate_causes"]
    assert "missing" in r["parameter_notes"][0]


def test_parameter_version_and_text_binding(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    conn.execute("UPDATE run_metrics SET value=0.6")
    spec = {"name": "optimizer", "evidence_id": comparison["evidence_id"], "text": "Adam"}
    r = compare(conn, comparison, paper_params=[spec])
    assert not r["candidate_causes"] and "absent" in r["parameter_notes"][0]
    conn.execute("INSERT INTO works(id,title,created_at,updated_at) VALUES ('other','Other','now','now')")
    conn.execute(
        "INSERT INTO document_versions(version_id,work_id,sha256,stored_path,original_name,size,mime,"
        "created_at) VALUES ('other','other','other','x','x',1,'application/pdf','now')"
    )
    conn.execute("UPDATE document_versions SET parser_version='test' WHERE version_id='other'")
    quote = "We used Adam with batch size 32."
    conn.execute(
        "INSERT INTO pages(version_id,page_index,text,text_sha256,char_count) VALUES ('other',0,?,?,?)",
        (quote, hashlib.sha256(quote.encode()).hexdigest(), len(quote)),
    )
    ev = evidence.create(conn, version_id="other", page_index=0, quote=quote, created_by="test")
    spec["evidence_id"] = ev["id"]
    r = compare(conn, comparison, paper_params=[spec])
    assert not r["candidate_causes"] and "another paper version" in r["parameter_notes"][0]


def test_m6_placeholders_pass_existing_gates(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    from researchos.services.writing import doc_snapshot, gates

    r = compare(conn, comparison)
    blocks = doc_snapshot.parse(f"Reported {r['evidence_ref']}; reproduced {r['metric_ref']}.")
    assert not gates.check(conn, blocks, "final").errors


@pytest.mark.parametrize("quote,expected", [("Accuracy was 0 %.", "deviation"), ("Accuracy was 1e-3.", "deviation")])
def test_zero_reference_and_scientific_notation(
    conn: sqlite3.Connection,
    comparison: dict[str, Any],
    quote: str,
    expected: str,
) -> None:
    conn.execute(
        "UPDATE pages SET text=?, text_sha256=?, char_count=?",
        (quote, hashlib.sha256(quote.encode()).hexdigest(), len(quote)),
    )
    ev = evidence.create(conn, version_id="version", page_index=0, quote=quote, created_by="test")
    comparison.update(evidence_id=ev["id"], tolerance={"kind": "relative", "value": 1})
    r = compare(conn, comparison)
    assert r["status"] == expected
    if "1e-3" in quote:  # review #16: one quantity, read in full (was rejected as unsupported);
        # the fixture's unit is "%", so 1e-3 % = 1e-5 as a fraction
        assert r["reported_value"] == pytest.approx(1e-5) and r["threshold"] == pytest.approx(1e-5)
        return
    if expected == "deviation":
        assert r["threshold"] == 0
        conn.execute("UPDATE run_metrics SET value=0")
        assert compare(conn, comparison)["status"] == "match"


def test_unicode_minus_keeps_the_sign(conn: sqlite3.Connection, comparison: dict[str, Any]) -> None:
    """Regression (review #17): a quoted "−80 %" (U+2212) is -0.8, not a match for the reproduced +0.8."""
    quote = "The change was −80 %."
    conn.execute(
        "UPDATE pages SET text=?, text_sha256=?, char_count=?",
        (quote, hashlib.sha256(quote.encode()).hexdigest(), len(quote)),
    )
    ev = evidence.create(conn, version_id="version", page_index=0, quote=quote, created_by="test")
    comparison.update(evidence_id=ev["id"], tolerance={"kind": "absolute", "value": 0})
    r = compare(conn, comparison)
    assert r["reported_value"] == pytest.approx(-0.8) and r["status"] == "deviation"


@pytest.mark.parametrize("value,unit,reported", [("1e-3ms", "ms", 0.001), ("−1,000", "1", -1000)])
def test_complete_signed_quantity_comparison(
    conn: sqlite3.Connection, comparison: dict[str, Any], value: str, unit: str, reported: float,
) -> None:
    quote = f"The measurement was {value}."
    conn.execute(
        "UPDATE pages SET text=?, text_sha256=?, char_count=?",
        (quote, hashlib.sha256(quote.encode()).hexdigest(), len(quote)),
    )
    ev = evidence.create(conn, version_id="version", page_index=0, quote=quote, created_by="test")
    comparison.update(evidence_id=ev["id"], unit=unit, tolerance={"kind": "absolute", "value": 0})
    conn.execute("UPDATE run_metrics SET value=1, unit=?", (unit,))
    result = compare(conn, comparison)
    assert result["status"] == "deviation"
    assert result["reported_value"] == pytest.approx(reported)
    conn.execute("UPDATE run_metrics SET value=?", (reported,))
    assert compare(conn, comparison)["status"] == "match"
