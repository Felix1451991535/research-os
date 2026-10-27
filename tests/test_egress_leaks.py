"""Egress gate, third pass (ADR-171): regressions on the exact failing paths of Codex's repros
(20261016-CodexReview/repros_42_55): agent tool output, the QA verifier, an index interrupted mid-build, a public
sample id equal to a private dataset id, and stored answers without provenance."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from researchos.contracts.errors import ApiError
from researchos.contracts.gateway import ToolSpec
from researchos.services.container import Services
from researchos.services.execution import datasets
from researchos.services.gateway import egress
from researchos.services.gateway.agent import AgentRun
from researchos.services.gateway.egress_index import INDEXED
from researchos.services.gateway.tools import ToolRegistry

from .fixtures.make_dataset import make
from .m1_helpers import configure_models
from .test_egress import call, cloud_only, dataset

RESULT = "Restricted cohort mean: 72.345; figure caption: patient outcome distribution"


def _agent(svc: Services, provider: str, derived: bool) -> AgentRun:
    steps = iter([{"action": "tool", "tool": "read_result", "args": {}}, {"action": "finish", "answer": "done"}])
    svc.gateway.providers[provider].responder = lambda req: json.dumps(next(steps))
    reg = ToolRegistry()
    reg.register(ToolSpec("read_result", "read a stored result", {"type": "object"}, True, False, "none",
                          derived_from_args=derived), lambda a: RESULT)
    return AgentRun(svc.gateway, reg, ["read_result"])


def test_47_tool_output_of_unknown_provenance_never_reaches_cloud(conn: sqlite3.Connection, svc: Services,
                                                                  tmp_path: Path) -> None:
    configure_models(conn, svc)
    providers = cloud_only(svc)
    dataset(conn, tmp_path, "private", "local_only")
    with pytest.raises(ApiError) as e:  # step 2 carries the tool output: no local model -> blocked, nothing sent
        _agent(svc, "deepseek", derived=False).run(conn, "summarize the result")
    assert e.value.code == "egress_blocked" and "read_result" in e.value.message
    assert not any("72.345" in m.content for c in providers["deepseek"].calls for m in c.messages)


def test_47_tool_output_goes_local_or_stays_cloud_when_derived_from_args(conn: sqlite3.Connection, svc: Services,
                                                                        tmp_path: Path) -> None:
    configure_models(conn, svc)
    providers = cloud_only(svc, local=("zhipu",))
    providers["zhipu"].responder = lambda req: json.dumps({"action": "finish", "answer": "done"})
    res = _agent(svc, "deepseek", derived=False).run(conn, "summarize the result")
    assert res.status == "finished" and providers["zhipu"].calls  # the second step went to the local model
    assert not any("72.345" in m.content for c in providers["deepseek"].calls for m in c.messages)
    cloud_only(svc)  # a pure computation tool keeps cloud routing
    res = _agent(svc, "deepseek", derived=True).run(conn, "compute")
    assert res.status == "finished"


def test_47_actual_qa_verifier_inherits_the_writer_restriction(conn: sqlite3.Connection, svc: Services,
                                                               tmp_path: Path) -> None:
    from researchos.services.research.qa import QAContext, _verify_support
    from researchos.services.research.qa_validate import Checked

    configure_models(conn, svc)
    providers = cloud_only(svc, local=("zhipu",))
    ds = dataset(conn, tmp_path, "private", "local_only")
    writer = call(svc, conn, "Analyze " + ds["id"])
    assert writer.provider == "zhipu" and writer.restricted == (ds["id"],)
    providers["deepseek"].responder = lambda req: '{"results":[{"id":"S1","supported":true}]}'
    status = _verify_support(svc.gateway, conn, QAContext("Analyze " + ds["id"], None, "test", None), writer,
                             Checked(kept=[{"text": "Cohort mean 72.345", "evidence": ["E1"]}]),
                             {"E1": SimpleNamespace(text="Cohort mean 72.345")}, [])  # type: ignore[dict-item]
    # the verifier must be another vendor than the writer; the only one left is cloud -> nothing is sent and the
    # answer is labelled unverified (before the fix the cloud verifier received the claim)
    assert status == "unverified" and not providers["deepseek"].calls


def test_53_interrupted_index_is_retried_and_restricts_meanwhile(conn: sqlite3.Connection, svc: Services,
                                                                 tmp_path: Path) -> None:
    configure_models(conn, svc)
    providers = cloud_only(svc)
    ds = dataset(conn, tmp_path, "private", "local_only")
    conn.execute("DELETE FROM dataset_fingerprints WHERE dataset_id=?", (ds["id"],))
    table = tmp_path / "private" / json.loads((tmp_path / "private" / "manifest.json").read_text())["table"]
    table.rename(table.with_suffix(".parked"))
    d = egress.check(conn, (), ["an unrelated question"])
    assert d.local_only and d.how == "index incomplete"  # unindexed restricted data may be anywhere
    assert not conn.execute("SELECT 1 FROM dataset_fingerprints WHERE token=? AND dataset_id=?",
                            (INDEXED, ds["id"])).fetchone()
    table.with_suffix(".parked").rename(table)
    with pytest.raises(ApiError, match="egress_blocked|local_only"):
        call(svc, conn, "private sample s03-c2-w12 has mean 72.345")  # retried, complete, detected
    assert not providers["deepseek"].calls and conn.execute(
        "SELECT 1 FROM dataset_fingerprints WHERE token=? AND dataset_id=?", (INDEXED, ds["id"])).fetchone()


def test_54_public_content_cannot_mask_a_private_dataset_id(conn: sqlite3.Connection, svc: Services,
                                                            tmp_path: Path) -> None:
    configure_models(conn, svc)
    providers = cloud_only(svc)
    private = dataset(conn, tmp_path, "private", "local_only")
    make(tmp_path / "public")
    manifest = json.loads((tmp_path / "public" / "manifest.json").read_text())
    table = tmp_path / "public" / manifest["table"]
    with np.load(table, allow_pickle=False) as z:
        arrays: dict[str, Any] = {k: z[k] for k in z.files}
    arrays["sample_id"] = arrays["sample_id"].astype("U100")
    arrays["sample_id"][0] = private["id"]
    np.savez(table, **arrays)
    manifest.update(egress="cloud_ok", name="public")
    manifest["files"][0]["sha256"] = hashlib.sha256(table.read_bytes()).hexdigest()
    (tmp_path / "public" / "manifest.json").write_text(json.dumps(manifest))
    public = datasets.register(conn, tmp_path / "public", "test")
    with pytest.raises(ApiError, match="local_only"):
        call(svc, conn, "Restricted dataset " + private["id"] + " has mean 72.345", (("dataset", public["id"]),))
    assert not providers["deepseek"].calls


def test_55_answers_without_provenance_fail_closed(conn: sqlite3.Connection, svc: Services, tmp_path: Path) -> None:
    configure_models(conn, svc)
    providers = cloud_only(svc)
    private = dataset(conn, tmp_path, "private", "local_only")
    conn.execute("INSERT INTO answers(id,question,status,body,created_at) VALUES ('oldans',?,'answered',?,'t')",
                 (private["id"], json.dumps({"sentences": [{"text": "Cohort mean 72.345"}]})))
    with pytest.raises(ApiError, match="no recorded provenance"):
        call(svc, conn, "Cohort mean 72.345", (("answer", "oldans"),))
    conn.execute("INSERT INTO answers(id,question,status,body,created_at) VALUES ('newans','q','answered',?,'t')",
                 (json.dumps({"sentences": [], "egress_restricted": []}),))
    assert call(svc, conn, "a public summary", (("answer", "newans"),)).provider == "deepseek"
    assert providers["deepseek"].calls
