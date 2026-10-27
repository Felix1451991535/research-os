"""Host-drawn run figures (ADR-194): from saved predictions + host split mapping, registered as figure artifacts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from researchos.services.container import Services
from researchos.services.execution import run_figures

from .fixtures.make_dataset import make
from .m2_helpers import new_plan, plan_content, run

pytest.importorskip("matplotlib")
PNG = b"\x89PNG\r\n\x1a\n"


def test_classification_runs_get_host_figures(client: Any, conn: sqlite3.Connection, svc: Services, settings: Any,
                                              tmp_path: Path) -> None:
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    t = run(client, conn, svc, new_plan(client, plan_content(client, ds)))
    figs = {Path(a["path"]).name: a for a in t["run"]["artifacts"] if a["kind"] == "figure"}
    assert {"confusion_search_val.png", "per_subject_search_val.png"} <= set(figs)
    for a in figs.values():
        assert (Path(settings.data_dir) / a["path"]).read_bytes().startswith(PNG)


def _preds(d: Path, text: str) -> Path:
    (d / "predictions").mkdir(parents=True)
    (d / "predictions" / "search_val.csv").write_text(text)
    return d


def test_draw_is_deterministic_and_never_raises(tmp_path: Path) -> None:
    good = "sample_id,y_true,y_pred\n" + "".join(f"s{i},{i % 2},{(i // 3) % 2}\n" for i in range(12))
    a = _preds(tmp_path / "a", good)
    b = _preds(tmp_path / "b", good)
    cls = {"type": "classification"}
    assert run_figures.draw(a, cls, ["train", "search_val"])["figures"] == ["confusion_search_val.png"]
    run_figures.draw(b, cls, ["search_val"])
    out = run_figures.OUT
    assert (a / out / "confusion_search_val.png").read_bytes() == (b / out / "confusion_search_val.png").read_bytes()
    bad = _preds(tmp_path / "c", "not,a,prediction,file\n\x00\x01")
    assert run_figures.draw(bad, cls, ["search_val"])["figures"] == []
    assert "not a classification" in run_figures.draw(a, {"type": "regression"}, ["search_val"])["note"]
    many = _preds(tmp_path / "e", "sample_id,y_true,y_pred\n" + "".join(f"s{i},{i},{i}\n" for i in range(60)))
    assert run_figures.draw(many, cls, ["search_val"])["skipped"][0]["reason"].startswith("more than")


def test_run_figures_are_served_only_as_registered_and_unchanged(client: Any, conn: sqlite3.Connection,
                                                                 svc: Services, settings: Any, tmp_path: Path) -> None:
    """ADR-209: GET /runs/{id}/figures/{name} serves registered figure artifacts of that run only."""
    ds = client.post("/api/v1/datasets", json={"path": str(make(tmp_path / "d"))}).json()
    pid = new_plan(client, plan_content(client, ds))
    a = run(client, conn, svc, pid)["run"]
    b = run(client, conn, svc, pid, seed=1)["run"]
    r = client.get(f"/api/v1/runs/{a['id']}/figures/confusion_search_val.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(PNG)
    assert client.get(f"/api/v1/runs/{a['id']}/figures/run.json").status_code == 404  # not a figure
    assert client.get("/api/v1/runs/nope/figures/confusion_search_val.png").status_code == 404
    path = Path(settings.data_dir) / "runs" / b["id"] / "host_figures" / "confusion_search_val.png"
    path.write_bytes(path.read_bytes() + b"tampered")
    assert client.get(f"/api/v1/runs/{b['id']}/figures/confusion_search_val.png").status_code == 404
