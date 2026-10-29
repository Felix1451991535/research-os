"""Diagnostics bundle (ADR-230): the zip lists only allow-listed entries and planted secrets, paths, user names and
e-mail addresses are redacted in every entry (logs incl. the UTF-16 install.log, system facts, task errors). Planted
values are assembled at run time so this file itself contains none of them."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from researchos.kernel.config import Settings
from researchos.kernel.logsetup import register_secret
from researchos.services.ops import diagnostics
from researchos.services.ops.diagnostics import ALLOWED, Redactor, build_bundle, read_tail

from .conftest import Client

API_KEY = "sk-" + "Planted" + "0123456789abcdef"
KNOWN_SECRET = "relay" + "-known-" + "secret-42"
EMAIL = "someone" + "@" + "university-mail.org"
WIN_PROFILE = "C:" + "\\Users\\" + "Alice" + "\\AppData\\Local\\Temp\\x.log"
BEARER = "Authorization: Bearer " + "tok" * 8


def _installed(tmp_path: Path) -> tuple[Path, Path]:
    inst = tmp_path / "Research OS"
    (inst / "app").mkdir(parents=True)
    (inst / "Uninstall.exe").write_bytes(b"")
    return inst, inst / "app"


def _plant_logs(settings: Settings, inst: Path) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    line = f"stored {settings.data_dir}/library/a.pdf key={API_KEY} {BEARER} mail {EMAIL} {KNOWN_SECRET}\n"
    (settings.logs_dir / "update.log").write_text(line + f"home {Path.home()}/notes.txt\n", encoding="utf-8")
    (settings.logs_dir / "launcher.log").write_text(f"temp {WIN_PROFILE}\n", encoding="utf-8")
    (settings.logs_dir / "api.log").write_text("not allow-listed\n", encoding="utf-8")
    (inst / "install.log").write_bytes(b"\xff\xfe" + f"install to {inst}\r\napi_key: {API_KEY}\r\n".encode("utf-16-le"))


def _plant_task(conn: Any) -> None:
    conn.execute(
        "INSERT INTO tasks (id, kind, idempotency_key, trace_id, state, payload, payload_hash, resource_class, "
        "error_code, error_message, created_at, updated_at) VALUES ('t1', 'lit.search', 'k1', 'tr1', 'failed', '{}', "
        "'h', 'io', 'provider_error', ?, '2026-10-28T00:00:00Z', '2026-10-28T00:00:01Z')",
        (f"upstream said {API_KEY} for {EMAIL}",))
    conn.commit()


def _entries(body: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        return {n: z.read(n).decode("utf-8") for n in z.namelist()}


def test_bundle_is_allow_listed_and_redacted(settings: Settings, conn: Any, tmp_path: Path) -> None:
    register_secret(KNOWN_SECRET)
    inst, app_root = _installed(tmp_path)
    _plant_logs(settings, inst)
    _plant_task(conn)
    name, body = build_bundle(settings, conn, app_root=app_root)
    files = _entries(body)
    assert name.startswith("ResearchOS-diagnostics-") and name.endswith(".zip")
    assert set(files) == ALLOWED - {"logs/install.prev.log"}  # api.log is never included; no prev log was planted
    blob = "\n".join(files.values())
    for planted in (API_KEY, KNOWN_SECRET, EMAIL, "Alice", "tok" * 8, str(settings.data_dir), str(inst),
                    str(Path.home())):
        assert planted not in blob, planted
    assert "<data>/library/a.pdf" in files["logs/update.log"] and "<home>/notes.txt" in files["logs/update.log"]
    assert "<email>" in files["logs/update.log"] and "[REDACTED]" in files["logs/update.log"]
    assert "Users\\<user>\\AppData" in files["logs/launcher.log"]
    assert "install to <install>" in files["logs/install.log"]  # UTF-16LE decoded, then redacted
    (task,) = json.loads(files["task_errors.json"])
    assert task["error_code"] == "provider_error" and "<email>" in task["error_message"]
    assert json.loads(files["system.json"])["installed_copy"] is True
    assert "NOT included" in files["README.txt"]


def test_outside_an_install_only_data_logs_are_read(settings: Settings, conn: Any, tmp_path: Path) -> None:
    _name, body = build_bundle(settings, conn, app_root=tmp_path / "src")
    assert set(_entries(body)) == {"version.json", "system.json", "task_errors.json", "README.txt"}


def test_only_allow_listed_entries_can_be_written() -> None:
    with pytest.raises(ValueError, match="allow-listed"):
        diagnostics._zip([("secrets.env", "x")])


def test_log_tail_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics, "LOG_TAIL_LINES", 3)
    p = tmp_path / "big.log"
    p.write_text("".join(f"line {i}\n" for i in range(100)), encoding="utf-8")
    assert read_tail(p) == "line 97\nline 98\nline 99\n"
    assert read_tail(tmp_path / "missing.log") is None


def test_redactor_handles_both_separators_and_case(tmp_path: Path) -> None:
    red = Redactor({"data": Path("D:/Research OS/data")})
    assert red("at d:\\research os\\DATA\\x and D:/Research OS/data/y") == "at <data>\\x and <data>/y"
    assert red(f"/home/{'someuser'}/x") == "/home/<user>/x"


def test_http_download_needs_a_session(client: Client, anon: Client) -> None:
    r = client.get("/api/v1/diagnostics/bundle")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"].startswith('attachment; filename="ResearchOS-diagnostics-')
    assert set(_entries(r.content)) <= ALLOWED
    assert anon.get("/api/v1/diagnostics/bundle").status_code == 401


def test_json_entries_are_redacted_before_serialisation(settings: Settings, conn: Any, tmp_path: Path) -> None:
    """Review #89: json.dumps doubles backslashes, so a Windows path (or a secret containing a quote) redacted only
    after serialisation slipped through. Strings are now redacted first."""
    secret = 'pass"word' + "-planted-77"
    register_secret(secret)
    win_data = str(settings.data_dir).replace("/", "\\")
    conn.execute(
        "INSERT INTO tasks (id, kind, idempotency_key, trace_id, state, payload, payload_hash, resource_class, "
        "error_code, error_message, created_at, updated_at) VALUES ('t2', 'lit.search', 'k2', 'tr2', 'failed', '{}', "
        "'h', 'io', 'io_error', ?, '2026-10-28T00:00:00Z', '2026-10-28T00:00:01Z')",
        (f"cannot open {win_data}\\library\\a.pdf ({secret})",))
    conn.commit()
    _name, body = build_bundle(settings, conn, app_root=tmp_path / "src")
    (task,) = json.loads(_entries(body)["task_errors.json"])
    assert task["error_message"] == "cannot open <data>\\library\\a.pdf ([REDACTED])"


def test_redactor_tree_redacts_keys_and_nested_values() -> None:
    red = Redactor({"data": Path("D:/PrivateLab/ResearchOS/data")})
    out = red.tree({"D:\\PrivateLab\\ResearchOS\\data\\k": [EMAIL, {"n": 1, "p": "d:\\privatelab\\researchos\\data"}]})
    assert out == {"<data>\\k": ["<email>", {"n": 1, "p": "<data>"}]}


def test_escaped_windows_paths_are_redacted() -> None:
    """Review #97: an exception's repr (or JSON text) has the backslashes doubled before the redactor sees it."""
    red = Redactor({"data": Path("D:\\PrivateLab\\ResearchOS\\data")})
    for sep in ("\\", "\\\\", "\\\\\\\\", "/"):
        path = sep.join(("D:", "PrivateLab", "ResearchOS", "data", "a.pdf"))
        assert red(f"open {path}") == f"open <data>{sep}a.pdf", sep


def test_a_real_file_not_found_error_in_task_errors_is_redacted(settings: Settings, conn: Any,
                                                               tmp_path: Path) -> None:
    """Review #97: str(FileNotFoundError) quotes the file name with repr, which doubles Windows backslashes."""
    win_data = str(settings.data_dir).replace("/", "\\")
    err = str(FileNotFoundError(2, "No such file or directory", win_data + "\\library\\a.pdf"))
    assert "\\\\library" in err  # the doubled form is what reaches task_errors
    conn.execute(
        "INSERT INTO tasks (id, kind, idempotency_key, trace_id, state, payload, payload_hash, resource_class, "
        "error_code, error_message, created_at, updated_at) VALUES ('t3', 'lit.import', 'k3', 'tr3', 'failed', '{}', "
        "'h', 'io', 'io_error', ?, '2026-10-28T00:00:00Z', '2026-10-28T00:00:01Z')", (err,))
    conn.commit()
    _name, body = build_bundle(settings, conn, app_root=tmp_path / "src")
    text = _entries(body)["task_errors.json"]
    (task,) = json.loads(text)
    assert task["error_message"] == "[Errno 2] No such file or directory: '<data>\\\\library\\\\a.pdf'"
    assert settings.data_dir.name not in text.replace("<data>", "")
