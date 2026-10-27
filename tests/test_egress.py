"""Egress gate (ADR-159): content from local_only datasets never reaches a cloud provider - declared or detected."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from researchos.contracts.errors import ApiError
from researchos.contracts.gateway import TaskType
from researchos.services.container import Services
from researchos.services.execution import datasets
from researchos.services.gateway import egress
from researchos.services.gateway.prompts import PromptTemplate
from researchos.services.gateway.router import Signals

from .fixtures.make_dataset import make
from .m1_helpers import configure_models

P = PromptTemplate("egress_probe", "1", "system", "{q}")


def ok(text: str) -> tuple[Signals, Any]:
    return Signals(validators_passed=1.0, self_reported=0.95), json.loads(text)


def dataset(conn: sqlite3.Connection, tmp: Path, name: str, egress_policy: str | None) -> dict[str, Any]:
    d = make(tmp / name)
    m = json.loads((d / "manifest.json").read_text())
    m["name"] = name
    if egress_policy is not None:
        m["egress"] = egress_policy
    (d / "manifest.json").write_text(json.dumps(m))
    return dict(datasets.register(conn, d, "test"))


def cloud_only(svc: Services, local: tuple[str, ...] = ()) -> dict[str, Any]:
    for name, p in svc.gateway.providers.items():
        p.location = "local" if name in local else "cloud"
        p.responder = lambda req: '{"ok": true, "conf": 0.95}'
    return dict(svc.gateway.providers)


def call(svc: Services, conn: sqlite3.Connection, q: str, refs: tuple[tuple[str, str], ...] = ()) -> Any:
    return svc.gateway.call(conn, task_type=TaskType.ROUTINE_TEXT, prompt=P, variables={"q": q}, validate=ok,
                            data_refs=refs)


def test_registration_records_policy_fail_closed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    assert egress.policy_of(dataset(conn, tmp_path, "unstated", None)["governance"]) == "local_only"
    assert egress.policy_of(dataset(conn, tmp_path, "open", "cloud_ok")["governance"]) == "cloud_ok"
    with pytest.raises(ApiError, match="egress must be one of"):
        dataset(conn, tmp_path, "bad", "anywhere")
    n = conn.execute("SELECT COUNT(*) FROM dataset_fingerprints WHERE token='s00-c0-w0'").fetchone()[0]
    assert n == 2  # sample ids of both registered datasets are fingerprints


def test_declared_local_only_data_never_reaches_cloud(conn: sqlite3.Connection, svc: Services, tmp_path: Path) -> None:
    configure_models(conn, svc)
    providers = cloud_only(svc)
    secret = dataset(conn, tmp_path, "credentialed", "local_only")
    with pytest.raises(ApiError) as e:
        call(svc, conn, "summarize the cohort", (("dataset", secret["id"]),))
    assert e.value.code == "egress_blocked" and secret["id"] in e.value.message
    assert not any(p.calls for p in providers.values())  # nothing was sent anywhere
    ev = conn.execute("SELECT data FROM events WHERE kind='gateway.egress_restricted'").fetchone()
    assert json.loads(ev["data"])["datasets"] == [secret["id"]]
    open_ds = dataset(conn, tmp_path, "open", "cloud_ok")
    assert call(svc, conn, "summarize", (("dataset", open_ds["id"]),)).payload == {"ok": True, "conf": 0.95}


def test_planted_payload_is_detected_without_declaration(conn: sqlite3.Connection, svc: Services,
                                                          tmp_path: Path) -> None:
    configure_models(conn, svc)
    providers = cloud_only(svc, local=("zhipu",))
    dataset(conn, tmp_path, "credentialed", "local_only")
    r = call(svc, conn, "Explain why window s03-c2-w12. scored low")  # a sample id pasted into a question
    assert r.provider == "zhipu" and not providers["deepseek"].calls  # served locally, cloud skipped
    providers["zhipu"].location = "cloud"
    with pytest.raises(ApiError, match="egress_blocked|local_only"):
        call(svc, conn, "Explain why window s03-c2-w12. scored low")


def test_plan_and_run_references_resolve_to_their_dataset(conn: sqlite3.Connection, tmp_path: Path) -> None:
    secret = dataset(conn, tmp_path, "credentialed", None)
    conn.execute("INSERT INTO plans(id,title,created_at) VALUES ('pl','t','2026-10-16')")
    conn.execute("INSERT INTO plan_revisions(plan_id,revision,content,content_hash,created_at,created_by) VALUES "
                 "('pl',1,?,'h','2026-10-16','test')", (json.dumps({"dataset": {"id": secret["id"]}}),))
    d = egress.check(conn, (("plan", "pl@1"),), [])
    assert d.local_only and d.datasets == (secret["id"],) and d.how == "declared"
    assert egress.check(conn, (("plan", "missing@1"),), []).local_only  # unresolvable reference fails closed
    assert not egress.check(conn, (), ["nothing identifying here"]).local_only


def test_relay_and_loopback_urls_are_cloud_unless_owner_trusts_a_provider(svc: Services) -> None:
    """Regression (review #48): a loopback relay or endpoint may tunnel to a vendor; only owner trust makes local."""
    from researchos.services.gateway.providers.http import OpenAICompatProvider, RelayProvider

    assert OpenAICompatProvider("deepseek", "http://127.0.0.1:11434/v1/chat/completions").location == "cloud"
    assert RelayProvider("https://localhost:8443/v1").location == "cloud"
    gw = svc.gateway
    gw.providers["relay"] = RelayProvider("https://localhost:8443/v1")
    gw.settings.relay_for, gw.settings.trusted_local_providers = ["openai"], ["zhipu", "relay"]
    gw.providers["zhipu"].location = "cloud"
    assert gw.location("zhipu") == "local" and gw.location("openai") == "cloud"  # relay can never be trusted local


@pytest.mark.parametrize("text", ["sample:s03-c2-w12", "/samples/S03-C2-W12.csv", "see s03-c2-w12, row 4",
                                  "C:\\data\\s03-c2-w12.npz"])
def test_identifier_formats_are_detected(conn: sqlite3.Connection, tmp_path: Path, text: str) -> None:
    """Regression (review #49): prefixes, paths, extensions and case defeated whole-token matching."""
    dataset(conn, tmp_path, "credentialed", "local_only")
    assert egress.check(conn, (), [text]).local_only


def test_missing_index_is_rebuilt(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Regression (review #50): datasets registered before the index existed stayed invisible, even re-registered."""
    secret = dataset(conn, tmp_path, "credentialed", "local_only")
    conn.execute("DELETE FROM dataset_fingerprints WHERE dataset_id=?", (secret["id"],))
    assert egress.check(conn, (), [f"dataset {secret['id']} window s03-c2-w12"]).local_only  # lazy repair
    conn.execute("DELETE FROM dataset_fingerprints WHERE dataset_id=?", (secret["id"],))
    again = json.loads((tmp_path / "credentialed" / "manifest.json").read_text())
    assert datasets.register(conn, tmp_path / "credentialed", "test")["id"] == secret["id"] and again
    n = conn.execute("SELECT COUNT(*) FROM dataset_fingerprints WHERE dataset_id=?", (secret["id"],)).fetchone()[0]
    assert n > 10


def test_shared_sample_ids_follow_the_declared_public_dataset(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Regression (review #51): a public dataset sharing fixture sample ids with a restricted one was blocked."""
    public = dataset(conn, tmp_path, "open", "cloud_ok")
    secret = dataset(conn, tmp_path, "credentialed", "local_only")
    assert not egress.check(conn, (("dataset", public["id"]),), ["window s03-c2-w12"]).local_only
    assert egress.check(conn, (), ["window s03-c2-w12"]).local_only  # undeclared: ambiguous -> restricted
    mixed = egress.check(conn, (("dataset", public["id"]),), [f"window s03-c2-w12 of {secret['id']}"])
    assert mixed.local_only and mixed.datasets == (secret["id"],)  # unique ids still restrict


def test_unknown_or_unresolvable_reference_kinds_fail_closed(conn: sqlite3.Connection, tmp_path: Path) -> None:
    """Regression (review #52): ("artifact", id) and unknown kinds were silently ignored."""
    assert egress.check(conn, (("artifact", "missing"),), ["72.345"]).local_only
    assert egress.check(conn, (("figure", "x"),), []).local_only
    secret = dataset(conn, tmp_path, "credentialed", None)
    conn.execute("INSERT INTO answers(id,question,status,body,created_at) VALUES ('ans1','q','answered',?,'t')",
                 (json.dumps({"egress_restricted": [secret["id"]]}),))
    d = egress.check(conn, (("answer", "ans1"),), ["mean 72.345"])  # derived text, no identifiers
    assert d.local_only and d.datasets == (secret["id"],)


def test_restriction_is_returned_and_inherited(conn: sqlite3.Connection, svc: Services, tmp_path: Path) -> None:
    """Regression (review #47): derived content (a number, a caption) has no identifiers; dependent calls inherit the
    restriction from the result instead (QA verifier, next agent step)."""
    configure_models(conn, svc)
    providers = cloud_only(svc, local=("zhipu",))
    secret = dataset(conn, tmp_path, "credentialed", "local_only")
    first = call(svc, conn, "mean of window s03-c2-w12?")
    assert first.restricted == (secret["id"],) and first.provider == "zhipu"
    second = call(svc, conn, "the cohort mean was 72.345; write a caption", egress.refs_of(first.restricted))
    assert second.provider == "zhipu" and not providers["deepseek"].calls


def test_agent_run_keeps_the_restriction(conn: sqlite3.Connection, svc: Services, tmp_path: Path) -> None:
    from researchos.services.gateway.agent import AgentRun
    from researchos.services.gateway.tools import ToolRegistry

    configure_models(conn, svc)
    providers = cloud_only(svc, local=("zhipu",))
    providers["zhipu"].responder = lambda req: json.dumps({"action": "finish", "answer": "done"})
    secret = dataset(conn, tmp_path, "credentialed", "local_only")
    res = AgentRun(svc.gateway, ToolRegistry(), []).run(conn, "summarize", data_refs=(("dataset", secret["id"]),))
    assert res.status == "finished" and providers["zhipu"].calls and not providers["deepseek"].calls
