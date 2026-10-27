"""Review #75 (caps exceeded through a low input-token estimate) and #76 (cheap-only must not depend on tier labels),
each on the reported trigger. Mock providers only."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from researchos.contracts.errors import ApiError
from researchos.contracts.gateway import ChatResponse, TaskType
from researchos.kernel.db import jdump, new_id, utcnow
from researchos.services.container import Services
from researchos.services.gateway import catalog
from researchos.services.gateway.prompts import PromptTemplate
from researchos.services.gateway.router import Signals

from .m1_helpers import configure_models

P = PromptTemplate("t", "1", "system", "{q}")


def _ok(text: str) -> tuple[Signals, Any]:
    obj = json.loads(text)
    return Signals(validators_passed=1.0 if obj.get("ok") else 0.0), obj


def _policy(conn: sqlite3.Connection, **g: Any) -> None:
    conn.execute("INSERT OR REPLACE INTO settings_kv(key,value_json,updated_at) VALUES ('gateway',?,?)",
                 (jdump(g), utcnow()))
    conn.commit()


def _spent(conn: sqlite3.Connection, project: str, cny: float) -> None:
    conn.execute("INSERT INTO ledger_entries(id,call_id,cost_kind,state,month,reserved_cny,actual_cny,price_snapshot,"
                 "project_id,task_id,created_at) VALUES (?,NULL,'model_call','settled',?,?,?,'{}',?,NULL,?)",
                 (new_id("led"), utcnow()[:7], cny, cny, project, utcnow()))
    conn.commit()


def _v1_prices(conn: sqlite3.Connection) -> None:
    alias = next(a for a in catalog.list_all(conn) if a["alias"] == "ROUTINE_TEXT")
    catalog.upsert_price(conn, {"provider": alias["provider"], "model_id": alias["model_id"], "currency": "USD",
                                "input_per_mtok": 0.30, "output_per_mtok": 1.20, "official_input_per_mtok": 0.30,
                                "official_output_per_mtok": 1.20, "effective_date": "2026-10-18", "source": "test",
                                "verified": 1})
    catalog.upsert_fx(conn, "USD", 6.7249, "test", "2026-10-14", True)


def test_75_reservation_bounds_the_reported_usage(conn: sqlite3.Connection, svc: Services, mocks: dict) -> None:
    """Codex repro: project 6.7185 CNY spent, other project 6.7249, caps 6.7249 / project and 13.4498 / day, a
    2,000-char input billed as 2,000 in / 400 out tokens. The reservation must refuse (before: settled over both)."""
    configure_models(conn, svc)
    _v1_prices(conn)
    _policy(conn, premium_enabled=False, monthly_budget_cny=13.4498, per_task_budget_cny=6.7249,
            daily_budget_cny=13.4498, per_project_budget_cny=6.7249)
    _spent(conn, "p1", 6.7185)
    _spent(conn, "p2", 6.7249)
    mocks["deepseek"].complete = lambda req, key: ChatResponse('{"ok": true}', 2000, 400, "r75", "m")
    with pytest.raises(ApiError) as e:
        svc.gateway.call(conn, task_type=TaskType.ROUTINE_TEXT, prompt=P, variables={"q": "x" * 2000},
                         validate=_ok, project_id="p1", max_output_tokens=400)
    assert e.value.code in ("project_budget_exhausted", "daily_budget_exhausted", "monthly_budget_exhausted")
    total = conn.execute("SELECT SUM(actual_cny) FROM ledger_entries WHERE state='settled'").fetchone()[0]
    assert total <= 13.4498 + 1e-9


def test_75_usage_beyond_the_bound_is_recorded(conn: sqlite3.Connection, svc: Services, mocks: dict) -> None:
    configure_models(conn, svc)
    _v1_prices(conn)
    mocks["deepseek"].complete = lambda req, key: ChatResponse('{"ok": true}', 10, 5000, "r75b", "m")  # > max out
    svc.gateway.call(conn, task_type=TaskType.ROUTINE_TEXT, prompt=P, variables={"q": "x"}, validate=_ok,
                     max_output_tokens=100)
    ev = conn.execute("SELECT data FROM events WHERE kind='budget.overshoot'").fetchone()
    assert ev and json.loads(ev[0])["output_tokens"] == 5000


def test_76_allow_list_blocks_a_mislabelled_expensive_model(conn: sqlite3.Connection, svc: Services,
                                                             mocks: dict) -> None:
    """Codex repro: an expensive LONG_DOCUMENT_REASONING model labelled tier `routine`, routine validation failing:
    premium/top disabled is not enough; the allowed-models list is."""
    configure_models(conn, svc)
    conn.execute("UPDATE model_aliases SET tier='routine' WHERE alias='LONG_DOCUMENT_REASONING'")
    conn.commit()
    mocks["deepseek"].responder = lambda req: '{"ok": false}'
    mocks["anthropic"].responder = lambda req: '{"ok": true}'
    _policy(conn, premium_enabled=False, top_enabled=False)
    assert svc.gateway.call(conn, task_type=TaskType.ROUTINE_TEXT, prompt=P, variables={"q": "x"},
                            validate=_ok).alias == "LONG_DOCUMENT_REASONING"  # the hole the label leaves open
    cheap = [a["model_id"] for a in catalog.list_all(conn) if a["alias"] in ("ROUTINE_TEXT", "ROUTINE_TEXT_GLM")]
    _policy(conn, premium_enabled=False, top_enabled=False, allowed_models=cheap)
    calls = len(mocks["anthropic"].calls)
    with pytest.raises(ApiError):
        svc.gateway.call(conn, task_type=TaskType.ROUTINE_TEXT, prompt=P, variables={"q": "y"}, validate=_ok)
    assert len(mocks["anthropic"].calls) == calls  # never called


def test_76_policy_round_trip(client: Any) -> None:
    body = {"monthly_budget_cny": 10, "per_task_budget_cny": 1, "allowed_models": ["deepseek-flash", "glm-5.3-flash"]}
    assert client.put("/api/v1/gateway/policy", json=body).status_code == 200
    assert client.get("/api/v1/gateway/policy").json()["allowed_models"] == ["deepseek-flash", "glm-5.3-flash"]


def test_77_reservation_uses_the_limit_the_provider_will_send(conn: sqlite3.Connection, svc: Services,
                                                              mocks: dict) -> None:
    """Codex repro: request limit 1, transport minimum 16, 1 CNY per output token, caps 10. Before: reserved 1,
    billed 16. Now the request is normalized to 16 before reservation and refused before the provider is called."""
    configure_models(conn, svc)
    alias = next(a for a in catalog.list_all(conn) if a["alias"] == "ROUTINE_TEXT")
    catalog.upsert_price(conn, {"provider": alias["provider"], "model_id": alias["model_id"], "currency": "CNY",
                                "input_per_mtok": 0.0, "output_per_mtok": 1_000_000.0, "official_input_per_mtok": 0.0,
                                "official_output_per_mtok": 1_000_000.0, "effective_date": "2026-10-18",
                                "source": "test", "verified": 1})
    _policy(conn, premium_enabled=False, monthly_budget_cny=10, per_task_budget_cny=10, daily_budget_cny=10)
    mocks["deepseek"].min_output_tokens = 16
    calls = len(mocks["deepseek"].calls)
    with pytest.raises(ApiError) as e:
        svc.gateway.call(conn, task_type=TaskType.ROUTINE_TEXT, prompt=P, variables={"q": "x"}, validate=_ok,
                         max_output_tokens=1)
    assert e.value.status == 402 and len(mocks["deepseek"].calls) == calls  # rejected before HTTP
    snap = conn.execute("SELECT data FROM events WHERE kind='budget.overshoot'").fetchone()
    assert snap is None
