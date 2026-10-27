"""Single-file Windows installer (ADR-212/215): the staged app payload and the offline runtime helpers. The full
exe build (runtime download + makensis) runs in scripts/build_package.py, not in the test suite."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> object:
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def staged(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not (ROOT / "apps/web/dist/index.html").is_file():
        pytest.skip("web build missing")
    out = tmp_path_factory.mktemp("app")
    assert _load("build_package").main(["--stage-only", str(out)]) == 0  # type: ignore[attr-defined]
    return out


def test_app_payload_has_the_launcher_and_no_duplicate_or_dev_trees(staged: Path) -> None:
    for need in ("scripts/launch.vbs", "scripts/launch.ps1", "scripts/start.ps1", "scripts/stop.ps1",
                 "scripts/researchos.ico", "apps/web/dist/index.html", "services/api/researchos/__init__.py",
                 "workers/researchos_worker", "uv.lock"):
        assert (staged / need).exists(), need
    for absent in ("plugins", "tests", "examples", "data", ".venv", "release"):
        assert not (staged / absent).exists(), absent  # plugins ship as wheels; no data, no dev trees


def test_manifest_is_honest(staged: Path) -> None:
    m = json.loads((staged / "PACKAGE_MANIFEST.json").read_text())
    assert m["acceptance"]["windows_install"] == "NOT RUN"
    assert m["acceptance"]["setup_exe"] == "NOT RUN on Windows (compiled only)"
    bad = re.compile(r"secrets|\.env$|\.edf$|\.db$|\.mp4$|eegmmidb")
    assert [f for f in m["files"] if bad.search(f)] == []


def test_runtime_helpers() -> None:
    wr = _load("win_runtime")
    assert wr._key("zope.interface", "7.0") == wr._key("zope-interface", "7.0") == "zope_interface-7.0"  # type: ignore[attr-defined]
    pth = wr.PTH.splitlines()  # type: ignore[attr-defined]
    assert "import site" in pth and "Lib\\site-packages" in pth and "..\\services\\api" in pth and "..\\workers" in pth
    assert wr.PY_URL.startswith("https://www.python.org/ftp/python/3.12.")  # type: ignore[attr-defined]


def test_pure_wheel_check(tmp_path: Path) -> None:
    wr = _load("win_runtime")
    pure, native = tmp_path / "a-1.0-py3-none-any.whl", tmp_path / "b-1.0-py3-none-any.whl"
    with zipfile.ZipFile(pure, "w") as z:
        z.writestr("a/__init__.py", "")
    with zipfile.ZipFile(native, "w") as z:
        z.writestr("b/_c.pyd", "")
    assert wr._is_pure(pure) and not wr._is_pure(native)  # type: ignore[attr-defined]


def test_install_list_has_only_requirements_never_paths() -> None:
    """Found by listing the 0.1.2 exe: plugin wheels were listed by file name, which pip would resolve against the
    install dir (its working directory) and not find."""
    wr = _load("win_runtime")
    lines = wr.install_lines(["numpy==2.5.4"], ["researchos_plugin_diag_stats-0.1.0-py3-none-any.whl"])  # type: ignore[attr-defined]
    assert lines == ["numpy==2.5.4", "researchos-plugin-diag-stats==0.1.0"]
    assert all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*==[^\s/\\]+", ln) for ln in lines)
