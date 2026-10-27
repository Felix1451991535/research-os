"""M5 checkpoint 2: screening log + PRISMA numbers, rule and model suggestions (never decisions), saved searches
with catch-up triggers, new-paper alerts, auto-screen and opt-in metadata import, weekly retraction task."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from researchos.contracts.gateway import ChatRequest
from researchos.services.container import Services
from researchos.services.gateway import catalog, certification
from researchos.services.literature import monitor
from researchos.services.literature.screening_model import SCREEN_SUGGEST

from .m1_helpers import configure_models, drain
from .m5_helpers import FakeSource, rec, register

CRITERIA = {
    "include": ["heart rate variability in adults"],
    "exclude": ["animal studies"],
    "rules": [
        {
            "id": "old",
            "field": "year",
            "op": "lte",
            "value": 2014,
            "decision": "exclude",
            "reason": "published before 2015",
        },
        {
            "id": "rat",
            "field": "text",
            "op": "contains",
            "value": "rats",
            "decision": "exclude",
            "reason": "animal study",
        },
    ],
}


@pytest.fixture
def src(svc: Services) -> FakeSource:
    s = FakeSource(
        "fake_a",
        hits={
            "hrv": [
                rec("fake_a", "1", "HRV in adults", "10.5555/m.1"),
                rec("fake_a", "2", "HRV in rats", "10.5555/m.2"),
                rec("fake_a", "3", "Old HRV study", "10.5555/m.3", year=2010),
            ]
        },
    )
    register(svc, s)
    return s


def review(client: Any) -> str:
    r = client.post("/api/v1/reviews", json={"title": "HRV", "question": "Does HRV predict X?", "criteria": CRITERIA})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def found(client: Any) -> dict[str, Any]:
    return dict(client.post("/api/v1/literature/search", json={"text": "hrv", "sources": ["fake_a"]}).json())


def test_rules_suggest_users_decide_prisma_counts_decisions_only(
    client: Any, src: FakeSource, conn: sqlite3.Connection
) -> None:
    rid, res = review(client), found(client)
    ids = {c["record"]["title"]: c["id"] for c in res["candidates"]}
    client.post(f"/api/v1/reviews/{rid}/items", json={"candidate_ids": list(ids.values()), "search_run_id": res["id"]})
    sug = client.post(f"/api/v1/reviews/{rid}/suggestions", json={"candidate_ids": list(ids.values())}).json()
    assert sug["rules"] == {ids["HRV in rats"]: "exclude", ids["Old HRV study"]: "exclude"}
    p0 = client.get(f"/api/v1/reviews/{rid}/prisma").json()
    assert p0["records_identified"] == 3 and p0["records_screened"] == 0  # suggestions are not decisions
    for title, d, why in (
        ("HRV in rats", "exclude", "animal study"),
        ("Old HRV study", "exclude", "published before 2015"),
        ("HRV in adults", "include", "meets criteria"),
    ):
        r = client.post(
            f"/api/v1/reviews/{rid}/decisions",
            json={"candidate_id": ids[title], "stage": "title_abstract", "decision": d, "reason": why},
        )
        assert r.status_code == 201
    bad = client.post(
        f"/api/v1/reviews/{rid}/decisions",
        json={"candidate_id": ids["HRV in rats"], "stage": "full_text", "decision": "include", "reason": "x" * 5},
    )
    assert bad.json()["code"] == "not_eligible"
    client.post(
        f"/api/v1/reviews/{rid}/decisions",
        json={
            "candidate_id": ids["HRV in adults"],
            "stage": "full_text",
            "decision": "include",
            "reason": "full text meets criteria",
        },
    )
    p = client.get(f"/api/v1/reviews/{rid}/prisma").json()
    assert (p["records_screened"], p["records_excluded"], p["full_text_assessed"], p["included"]) == (3, 2, 1, 1)
    assert p["excluded_reasons"] == {"animal study": 1, "published before 2015": 1}
    with pytest.raises(sqlite3.IntegrityError):  # append-only screening log
        conn.execute("UPDATE screening_decisions SET decision='include'")


def test_model_suggestions_need_quotes_and_certification(client: Any, svc: Services, conn: sqlite3.Connection) -> None:
    def respond(req: ChatRequest) -> str:
        return json.dumps(
            {
                "items": [
                    {
                        "id": "C1",
                        "decision": "include",
                        "criterion": "include[0]",
                        "quote": "HRV in adults",
                        "reason": "ok",
                    },
                    {
                        "id": "C2",
                        "decision": "exclude",
                        "criterion": "exclude[0]",
                        "quote": "mice were used",
                        "reason": "x",
                    },
                ]
            }
        )

    for name in ("deepseek", "zhipu"):
        svc.gateway.providers[name].responder = respond  # type: ignore[attr-defined]
    register(
        svc,
        FakeSource(
            "fake_a",
            hits={
                "hrv": [
                    rec("fake_a", "1", "HRV in adults", "10.5555/q.1"),
                    rec("fake_a", "2", "HRV in rats", "10.5555/q.2"),
                ]
            },
        ),
    )
    configure_models(conn, svc, certify=False)
    rid, res = review(client), found(client)
    ids = [c["id"] for c in res["candidates"]]
    client.post(f"/api/v1/reviews/{rid}/items", json={"candidate_ids": ids})
    first = client.post(f"/api/v1/reviews/{rid}/suggestions", json={"candidate_ids": ids, "use_model": True}).json()
    assert "not certified" in first["model"]["error"]
    for a in catalog.list_all(conn):
        certification.record(
            conn,
            "test",
            a["model_id"],
            SCREEN_SUGGEST.hash,
            {"locate_rate": 1, "support_precision": 1, "refusal_rate": 1},
        )
    out = client.post(f"/api/v1/reviews/{rid}/suggestions", json={"candidate_ids": ids, "use_model": True}).json()
    assert out["model"]["suggested"] == {ids[0]: "include"} and out["model"]["dropped"] == [ids[1]]
    assert client.get(f"/api/v1/reviews/{rid}/prisma").json()["records_screened"] == 0


def test_saved_search_trigger_run_alert_autoscreen_import(
    client: Any, svc: Services, src: FakeSource, conn: sqlite3.Connection
) -> None:
    rid = review(client)
    r = client.post(
        "/api/v1/saved-searches",
        json={
            "name": "HRV watch",
            "kind": "query",
            "query": {"text": "hrv"},
            "sources": ["fake_a"],
            "cadence": "daily",
            "review_id": rid,
            "auto_import": True,
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    now = datetime.now(UTC)
    assert monitor.trigger(conn, svc.kinds, now) == 2  # saved search + weekly retraction check
    assert monitor.trigger(conn, svc.kinds, now) == 0  # same slots: no duplicates
    drain(conn, svc, max_tasks=5)
    alerts = client.get("/api/v1/alerts").json()
    new = next(a for a in alerts if a["kind"] == "new_papers")
    assert new["data"]["new"] == 3 and new["data"]["auto_excluded"] == 2 and new["data"]["imported"] == 1
    assert conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 1
    items = client.get(f"/api/v1/reviews/{rid}").json()["items"]
    assert len(items) == 3 and all(i["screening"]["title_abstract"]["decision"] is None for i in items)
    # ten days asleep -> exactly one catch-up run; nothing new -> no new alert; catch-up uses from_year
    later = now + timedelta(days=10)
    assert monitor.trigger(conn, svc.kinds, later) == 2
    drain(conn, svc, max_tasks=5)
    assert len([a for a in client.get("/api/v1/alerts").json() if a["kind"] == "new_papers"]) == 1
    assert src.queries[-1].filters.get("from_year") == str(now.year)
    nxt = monitor.get(conn, sid)["next_run_at"]
    assert nxt > (later + timedelta(hours=23)).isoformat()


def test_author_watch_passes_author_filter(
    client: Any, svc: Services, src: FakeSource, conn: sqlite3.Connection
) -> None:
    src.hits["Ada Lovelace"] = [rec("fake_a", "9", "Notes on the engine", "10.5555/m.9")]
    r = client.post(
        "/api/v1/saved-searches",
        json={
            "name": "Ada",
            "kind": "author",
            "query": {"author": "Ada Lovelace"},
            "sources": ["fake_a"],
            "cadence": "manual",
        },
    ).json()
    t = client.post(f"/api/v1/saved-searches/{r['id']}/run").json()
    drain(conn, svc, max_tasks=2)
    assert t["kind"] == "literature.saved_search" and src.queries[-1].filters == {"author": "Ada Lovelace"}
    assert client.get("/api/v1/alerts").json()[0]["data"]["new"] == 1
    bad = client.post(
        "/api/v1/saved-searches",
        json={"name": "x", "kind": "author", "query": {"text": "x"}, "sources": ["fake_a"], "cadence": "manual"},
    )
    assert bad.json()["code"] == "invalid_saved_search"
