"""M4 / G5: canary sweep through the real worker + template subprocesses; every injected fault must trip its detector
and the clean run must pass. Plans still need owner approval (no bypass)."""

from __future__ import annotations

import sqlite3
from typing import Any

from researchos.services.container import Services

from .conftest import Client
from .m1_helpers import drain
from .m2_helpers import approve


def test_canary_sweep_all_detectors_fire(client: Client, conn: sqlite3.Connection, svc: Services) -> None:
    c = client.post("/api/v1/canary").json()
    assert client.post(f"/api/v1/canary/{c['id']}/start").json()["code"] == "plan_not_approved"
    for a in c["approve"]:
        approve(client, a["subject_type"], a["subject_id"])
    r = client.post(f"/api/v1/canary/{c['id']}/start")
    assert r.status_code == 202 and r.json()["overall"] == "pending"
    drain(conn, svc, max_tasks=20)
    s = client.get(f"/api/v1/canary/{c['id']}").json()
    bad = [f for f in s["faults"] if f["result"] != "pass"]
    assert s["overall"] == "pass", bad
    assert len(s["faults"]) == 9 and s["not_covered"]
    # starting again is idempotent (no duplicate runs)
    assert client.post(f"/api/v1/canary/{c['id']}/start").json()["overall"] == "pass"


def test_measure_latency_and_suspend_voids_timing(tmp_path: Any) -> None:
    import json

    from researchos.contracts.verification import HealthSignal
    from researchos.sdk import measure_latency
    from researchos.services.execution.ingest import _metrics
    from researchos.services.verification.view import DirRunView

    s = measure_latency(lambda: sum(range(100)), warmup=2, repeats=20)
    assert 0 < s.p50_ms <= s.p95_ms and s.repeats == 20
    (tmp_path / "summary.json").write_text(
        json.dumps(
            [
                {
                    "name": "m.latency_b1_p50",
                    "split": "device:cpu",
                    "value": 1.5,
                    "unit": "ms",
                    "verification": "self_reported",
                },
                {
                    "name": "m.model_bytes",
                    "split": "device:cpu",
                    "value": 10,
                    "unit": "bytes",
                    "verification": "self_reported",
                },
            ]
        )
    )
    view = DirRunView(tmp_path, {}, {})
    sig = HealthSignal("host_suspend@1", "info", "suspend", {"gap_s": 900})
    out = {m["name"]: m for m in _metrics(view, [sig])}
    assert out["m.latency_b1_p50"]["value"] is None and out["m.latency_b1_p50"]["reported_value"] == 1.5
    assert out["m.model_bytes"]["value"] == 10
    assert {m["name"]: m for m in _metrics(view, [])}["m.latency_b1_p50"]["value"] == 1.5
