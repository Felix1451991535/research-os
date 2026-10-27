"""M0 hardening (docs/ARCHITECTURE.md (hardening)): key isolation in child env, env scrub, trace ids,
request preflight."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from researchos.kernel.config import Settings
from researchos.platform.env import UnsafeEnvError, child_env, scrub_process_env
from researchos.platform.process import spawn
from researchos.services.gateway.keystore import SecretStore

from .conftest import Client


def test_child_env_never_carries_provider_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak-123456")
    monkeypatch.setenv("SOME_TOKEN", "x")
    env = child_env(tmp_path / "home", {"SEED": "1"})
    assert "OPENAI_API_KEY" not in env and "SOME_TOKEN" not in env and env["SEED"] == "1"
    assert env["HOME"].startswith(str(tmp_path)) and env["PYTHONUTF8"] == "1"
    with pytest.raises(UnsafeEnvError):
        child_env(tmp_path / "home", {"MY_API_KEY": "x"})
    with pytest.raises(UnsafeEnvError):
        spawn([sys.executable, "-c", "pass"], cwd=tmp_path, env={**env, "ANTHROPIC_API_KEY": "sk-x"})


def test_spawned_child_cannot_see_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-secret-999999")
    out = tmp_path / "env.txt"
    code = f"import os; open({str(out)!r},'w').write(repr(sorted(os.environ)))"
    c = spawn([sys.executable, "-c", code], cwd=tmp_path, env=child_env(tmp_path / "home"),
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    c.popen.wait(timeout=20)
    assert "DEEPSEEK_API_KEY" not in out.read_text()


def test_keystore_capture_then_scrub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = {"ZHIPU_API_KEY": "abc.secret-value-1", "HTTP_PROXY": "http://proxy", "PATH": os.environ["PATH"],
            "RESEARCHOS_DATA_DIR": "/d", "AWS_SECRET_ACCESS_KEY": "x"}
    monkeypatch.setattr(os, "environ", fake)
    ks = SecretStore(tmp_path)
    ks.capture_env()
    removed = scrub_process_env(tmp_path / "home", fake)
    assert ks.get("zhipu") == "abc.secret-value-1" and ks.status()["zhipu"]["configured"]
    assert "ZHIPU_API_KEY" not in fake and "AWS_SECRET_ACCESS_KEY" in removed and "HTTP_PROXY" in removed
    assert fake["RESEARCHOS_DATA_DIR"] == "/d" and fake["HOME"].startswith(str(tmp_path))


def test_trace_id_on_tasks_runs_and_events(client: Client) -> None:
    r = client.post("/api/v1/tasks", json={"kind": "system.sleep", "payload": {"seconds": 0}},
                    headers={"x-correlation-id": "trace-abc"})
    t = r.json()
    assert t["trace_id"] == "trace-abc"
    ev = client.get("/api/v1/events", params={"entity_id": t["id"]}).json()
    assert all(e["correlation_id"] == "trace-abc" for e in ev)


def test_request_preflight_rejects_oversized_body(client: Client, settings: Settings) -> None:
    big = "x" * (2 * 1024 * 1024 + 10)
    r = client.post("/api/v1/projects", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "payload_too_large"
