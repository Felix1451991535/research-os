"""Egress per literature source (ADR-232): what a search source would receive passes the central egress gate (ADR-159)
first, so content from a local_only dataset (detected in the query or declared by the caller) never reaches a
remote source; cloud_ok data and sources that declare location = "local" are unaffected. In-test sources only."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from researchos.services.container import Services
from researchos.services.literature import discovery

from .m5_helpers import FakeSource, rec, register
from .test_egress import dataset

SAMPLE = "s00-c0-w0"  # a sample id of the fixture dataset (one of its fingerprints)


def _ctx(svc: Services, *refs: tuple[str, str]) -> discovery.Ctx:
    return discovery.Ctx(svc.extensions, svc.keystore, "user:test", None, tuple(refs))


def _blocked(res: dict, name: str) -> bool:
    return str(res["per_source"][name].get("error", "")).startswith("egress_blocked:")


def test_local_only_sample_id_in_a_query_never_reaches_a_remote_source(conn: sqlite3.Connection, svc: Services,
                                                                       tmp_path: Path) -> None:
    secret = dataset(conn, tmp_path, "credentialed", "local_only")
    src = FakeSource("remote_src")
    register(svc, src)
    res = discovery.search(conn, _ctx(svc), f"heart rate {SAMPLE}", ["remote_src"])
    assert src.calls == [] and _blocked(res, "remote_src") and secret["id"] in res["per_source"]["remote_src"]["error"]
    ev = conn.execute("SELECT data FROM events WHERE kind='gateway.egress_blocked'").fetchone()
    assert json.loads(ev["data"]) == {"datasets": [secret["id"]], "how": "detected", "source": "remote_src"}
    res = discovery.search(conn, _ctx(svc), "heart rate", ["remote_src"], filters={"author": SAMPLE})
    assert src.calls == [] and _blocked(res, "remote_src")  # filters are sent too


def test_declared_local_only_reference_blocks_any_query(conn: sqlite3.Connection, svc: Services,
                                                        tmp_path: Path) -> None:
    secret = dataset(conn, tmp_path, "credentialed", "local_only")
    src = FakeSource("remote_src")
    register(svc, src)
    res = discovery.search(conn, _ctx(svc, ("dataset", secret["id"])), "a harmless query", ["remote_src"])
    assert src.calls == [] and _blocked(res, "remote_src")


def test_cloud_ok_data_and_local_sources_are_allowed(conn: sqlite3.Connection, svc: Services, tmp_path: Path) -> None:
    dataset(conn, tmp_path, "open", "cloud_ok")
    remote = FakeSource("remote_src")
    register(svc, remote)
    discovery.search(conn, _ctx(svc), f"heart rate {SAMPLE}", ["remote_src"])
    assert remote.calls == [f"heart rate {SAMPLE}"]  # only an open dataset carries this id
    dataset(conn, tmp_path, "credentialed", "local_only")
    local = FakeSource("local_index")
    local.location = "local"  # type: ignore[attr-defined]
    register(svc, local)
    res = discovery.search(conn, _ctx(svc), f"heart rate {SAMPLE}", ["local_index", "remote_src"])
    assert local.calls == [f"heart rate {SAMPLE}"] and "error" not in res["per_source"]["local_index"]
    assert remote.calls == [f"heart rate {SAMPLE}"] and _blocked(res, "remote_src")  # not sent a second time


def test_citation_chaining_seed_is_checked(conn: sqlite3.Connection, svc: Services, tmp_path: Path) -> None:
    dataset(conn, tmp_path, "credentialed", "local_only")
    src = FakeSource("remote_src", graph={"cited_by:10.5555/x": [rec("remote_src", "c", "Follow-up", "10.5555/c")]})
    register(svc, src)
    seed = rec("remote_src", "x", f"Notes on {SAMPLE}", "10.5555/x")
    res = discovery.chain(conn, _ctx(svc), seed, "cited_by", ["remote_src"])
    assert src.calls == [] and _blocked(res, "remote_src") and res["candidate_ids"] == []
    ok = discovery.chain(conn, _ctx(svc), rec("remote_src", "x", "Notes", "10.5555/x"), "cited_by", ["remote_src"])
    assert "error" not in ok["per_source"]["remote_src"] and len(ok["candidate_ids"]) == 1


def test_every_string_field_of_a_chaining_seed_is_checked(conn: sqlite3.Connection, svc: Services,
                                                          tmp_path: Path) -> None:
    """Review #90: the whole BibRecord goes to the source (OpenAlex sends source_id), so every string field is checked,
    not only title / identifiers / authors."""
    dataset(conn, tmp_path, "credentialed", "local_only")
    src = FakeSource("remote_src", graph={"cited_by:10.5555/x": [rec("remote_src", "c", "Follow-up", "10.5555/c")]})
    register(svc, src)
    seed = rec("remote_src", "x", "Notes", "10.5555/x")
    for field in ("source_id", "abstract", "venue", "url", "oa_pdf_url", "license", "license_source", "kind"):
        res = discovery.chain(conn, _ctx(svc), replace(seed, **{field: f"see {SAMPLE}"}), "cited_by", ["remote_src"])
        assert src.calls == [] and _blocked(res, "remote_src"), field
