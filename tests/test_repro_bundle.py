"""Reproducible plan bundle (ADR-192): every run incl. failures, sources by hash, never the data, hashed index."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from researchos.services.container import Services

from .fixtures.make_dataset import make
from .m2_helpers import new_plan, plan_content, run


def test_bundle_keeps_failures_and_hashes_everything(client: Any, conn: sqlite3.Connection, svc: Services,
                                                     tmp_path: Path) -> None:
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds))
    ok = run(client, conn, svc, pid)["run"]["id"]
    bad = run(client, conn, svc, pid, params={"mode": "crash"})["run"]["id"]
    r = client.post(f"/api/v1/plans/{pid}/bundle")
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["runs"] == 2 and out["failed_runs"] == 1
    blob = client.get(f"/api/v1/exports/{out['id']}/files/bundle.zip").content
    assert hashlib.sha256(blob).hexdigest() == out["files"][0]["sha256"]
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = set(z.namelist())
    index = json.loads(z.read("BUNDLE.json"))
    assert {f"runs/{ok}/run.json", f"runs/{ok}/metrics.jsonl", f"runs/{bad}/task.json", "plan/revision-1.json",
            "plan/approvals.json", "dataset/manifest.json", "REPRODUCE.md", "env/uv.lock"} <= names
    assert json.loads(z.read(f"runs/{bad}/task.json"))["state"] != "succeeded"  # failures are kept
    assert any(n.startswith("template/fault/") for n in names)  # source with the hash the runs used
    assert not [n for n in names if "/inputs/" in n or "/home/" in n]  # no materialised data
    assert all(hashlib.sha256(z.read(n)).hexdigest() == h for n, h in index["files"].items())
    assert set(index["files"]) == names - {"BUNDLE.json"}


def test_bundle_refusals(client: Any, tmp_path: Path) -> None:
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds))
    assert client.post(f"/api/v1/plans/{pid}/bundle").json()["code"] == "no_runs"
    assert client.post("/api/v1/plans/plan_nope/bundle").status_code == 404


def _bundle(client: Any, pid: str) -> tuple[zipfile.ZipFile, dict[str, Any]]:
    out = client.post(f"/api/v1/plans/{pid}/bundle").json()
    blob = client.get(f"/api/v1/exports/{out['id']}/files/bundle.zip").content
    return zipfile.ZipFile(io.BytesIO(blob)), out


def test_72_bundle_is_allow_listed_and_never_carries_data(client: Any, conn: sqlite3.Connection, svc: Services,
                                                         settings: Any, tmp_path: Path) -> None:
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds))
    rid = run(client, conn, svc, pid)["run"]["id"]
    rdir = Path(settings.data_dir) / "runs" / rid
    (rdir / "raw-copy.npz").write_bytes(b"training data")  # Codex repro
    (rdir / "cache").mkdir()
    (rdir / "cache" / "x.json").write_text("{}")
    (rdir / "big.json").write_bytes(b"[" + b"0," * 3_000_000 + b"0]")
    z, out = _bundle(client, pid)
    names = set(z.namelist())
    assert not {f"runs/{rid}/raw-copy.npz", f"runs/{rid}/cache/x.json", f"runs/{rid}/big.json"} & names
    skipped = {s["file"] for s in out["skipped"]}
    assert {f"runs/{rid}/raw-copy.npz", f"runs/{rid}/cache/x.json", f"runs/{rid}/big.json"} <= skipped
    assert f"runs/{rid}/run.json" in names and any(n.startswith(f"runs/{rid}/predictions/") for n in names)


def test_73_lock_only_when_it_matches_the_runs(client: Any, conn: sqlite3.Connection, svc: Services, settings: Any,
                                              tmp_path: Path) -> None:
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds))
    rid = run(client, conn, svc, pid)["run"]["id"]
    assert "env/uv.lock" in _bundle(client, pid)[0].namelist()  # recorded hash = installed lock
    mf = Path(settings.data_dir) / "runs" / rid / "run.json"
    manifest = json.loads(mf.read_text())
    mf.write_text(json.dumps({**manifest, "env_lock": "0" * 64}))
    z, out = _bundle(client, pid)
    assert "env/uv.lock" not in z.namelist()
    assert any(s["file"] == "env/uv.lock" and "0" * 64 in s["reason"] for s in out["skipped"])


def test_inputs_and_home_are_summarised_once_per_run(client: Any, conn: sqlite3.Connection, svc: Services,
                                                     settings: Any, tmp_path: Path) -> None:
    """Found while recording the release video: a matplotlib font cache under the run home listed dozens of files.
    inputs/ and home/ are excluded by design and reported once per run; other stray files stay listed (#72)."""
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds))
    rid = run(client, conn, svc, pid)["run"]["id"]
    cache = Path(settings.data_dir) / "runs" / rid / "home" / ".cache" / "fontconfig"
    cache.mkdir(parents=True)
    for i in range(20):
        (cache / f"{i}.cache-9").write_bytes(b"x")
    z, out = _bundle(client, pid)
    skipped = [s["file"] for s in out["skipped"] if s["file"].startswith(f"runs/{rid}/")]
    assert f"runs/{rid}/home/" in skipped and not any(".cache" in f for f in skipped)
    assert not [n for n in z.namelist() if "/home/" in n or "/inputs/" in n]
