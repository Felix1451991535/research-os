"""Plugin SDK (core 1.2): conformance suites reject violations; the reference plugin loads through real entry-point
machinery and works end-to-end through the host (search -> approved import -> export, dataset build, templates)."""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from researchos.contracts.datasources import BibRecord, DataSourceSpec, SearchPage, SearchQuery
from researchos.kernel.config import Settings
from researchos.sdk.conformance import BibliographicSourceConformance, TemplateConformance
from researchos.sdk.testing import FakeHttp, json_response
from researchos.services import plugins
from researchos.services.knowledge.http_fetch import HostHttp

from .conftest import Client
from .m1_helpers import drain

HELLO = Path(__file__).resolve().parents[1] / "examples" / "plugin_hello"
sys.path.insert(0, str(HELLO / "src"))
CROSSREF = (HELLO / "fixtures" / "crossref_search.json").read_text()


# ---------- negative conformance: a sloppy plugin must FAIL the suites ----------
class SloppySource:
    spec = DataSourceSpec("sloppy", "1", "bibliographic", "bad", hosts=("api.example.org",))

    def search(self, q: SearchQuery, http: Any, settings: Any) -> SearchPage:
        http.get("https://evil.example.net/track")  # undeclared host
        return SearchPage(
            (BibRecord("sloppy", "1", "T", identifiers=(("doi", "https://doi.org/10.1/ABC"),), venue=""),)
        )


class SloppyConformance(BibliographicSourceConformance):
    def make_source(self) -> SloppySource:
        return SloppySource()

    def make_http(self) -> FakeHttp:
        return FakeHttp({"https://evil.example.net": json_response("x", "{}")}, ("api.example.org", "evil.example.net"))

    def sample_query(self) -> SearchQuery:
        return SearchQuery("x")


def test_conformance_catches_bad_source() -> None:
    suite = SloppyConformance()
    with pytest.raises(AssertionError):
        suite.test_returns_normalized_records()  # '' venue + un-normalized DOI
    with pytest.raises(AssertionError):
        suite.test_only_declared_hosts()


def test_conformance_catches_template_reading_test(tmp_path: Path) -> None:
    import numpy as np

    root = tmp_path / "bad"
    root.mkdir()
    (root / "template.json").write_text('{"id":"bad","version":"1","entry":"run.py","params_schema":{}}')
    (root / "run.py").write_text(
        "from researchos.sdk import RunContext, SplitNotAvailable\nctx=RunContext.from_env()\n"
        "try:\n    ctx.data('test')\nexcept SplitNotAvailable:\n    pass\n"
        "ctx.report('m', 0.5, split='search_val', verification='self_reported')\n"
    )
    arr = {"X": np.zeros((2, 2)), "y": np.zeros(2), "group": np.array(["a", "b"]), "sample_id": np.array(["1", "2"])}

    class Suite(TemplateConformance):
        def template_root(self) -> Path:
            return root

        def params(self) -> dict[str, Any]:
            return {}

        def split_arrays(self) -> dict[str, dict[str, Any]]:
            return {"train": arr, "search_val": arr}

    with pytest.raises(AssertionError, match="outside the stage"):
        Suite().test_run_contract()


# ---------- host integration through entry points ----------
@dataclass
class EP:
    name: str
    value: str
    group: str

    @property
    def dist(self) -> Any:
        return type("D", (), {"name": "researchos-plugin-hello"})()

    def load(self) -> Any:
        import importlib

        mod, _, attr = self.value.partition(":")
        return getattr(importlib.import_module(mod), attr)


def _hello_eps() -> dict[str, list[EP]]:
    import tomllib

    eps = tomllib.loads((HELLO / "pyproject.toml").read_text())["project"]["entry-points"]
    return {g: [EP(n, v, g) for n, v in m.items()] for g, m in eps.items()}


@pytest.fixture
def hello(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Any:
    eps = _hello_eps()
    monkeypatch.setattr(plugins, "entry_points", lambda group: eps.get(group, []))

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/pdf/"):
            return httpx.Response(200, content=b"%PDF-1.4\n%synthetic\n")
        return httpx.Response(200, text=CROSSREF)

    monkeypatch.setattr(HostHttp, "default_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(HostHttp, "default_resolver", lambda host: ["93.184.216.34"])  # public address, no DNS
    monkeypatch.setattr(HostHttp, "_last", {})
    from researchos.services.wiring import build_services

    return build_services(settings, providers={})


@pytest.fixture
def hclient(hello: Any, settings: Settings) -> Client:
    from researchos.app.factory import create_app

    from .conftest import PASSWORD

    c = Client(create_app(settings, hello))
    tok = (settings.config_dir / "setup-token.txt").read_text().strip()
    c.post("/api/v1/auth/setup", json={"username": "a", "password": PASSWORD, "setup_token": tok})
    return c


def test_extensions_registered(hclient: Client) -> None:
    ext = hclient.get("/api/v1/extensions").json()
    assert ext["sources"] == ["hello_crossref"] and ext["dataset_providers"] == ["hello_synthetic"]
    assert ext["importers"] == ["hello_open_importer"] and ext["file_importers"] == ["hello_ris"]
    assert set(ext["exporters"]) == {"markdown", "hello_plaintext"} and ext["tools"] == ["hello_word_count"]
    assert ext["rejected"] == []


def test_search_import_requires_approval_and_is_idempotent(hclient: Client, conn: sqlite3.Connection) -> None:
    page = hclient.post("/api/v1/sources/hello_crossref/search", json={"text": "har", "page_size": 2}).json()
    assert len(page["records"]) == 2 and page["records"][0]["identifiers"] == [["doi", "10.5555/hello.0001"]]
    body = {"records": page["records"]}
    r = hclient.post("/api/v1/imports/hello_open_importer", json=body)
    assert r.status_code == 409 and r.json()["code"] == "import_not_approved"
    h = r.json()["details"]["subject_id"]
    assert (
        hclient.post("/api/v1/approvals", json={"subject_type": "knowledge_import", "subject_id": h}).status_code == 201
    )
    res = hclient.post("/api/v1/imports/hello_open_importer", json=body).json()["results"]
    assert [x["status"] for x in res] == ["created", "created"]
    assert res[0]["version_id"] and res[1]["version_id"] is None  # PDF only for the CC-BY record
    assert hclient.post("/api/v1/imports/hello_open_importer", json=body).status_code == 409  # approval consumed
    hclient.post("/api/v1/approvals", json={"subject_type": "knowledge_import", "subject_id": h})
    again = hclient.post("/api/v1/imports/hello_open_importer", json=body).json()["results"]
    assert [x["status"] for x in again] == ["reused", "reused"] and again[0]["work_id"] == res[0]["work_id"]
    assert conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 2


def test_undeclared_capability_denied(hello: Any) -> None:
    from researchos.contracts.http import HttpDenied

    http = HostHttp(hello.extensions.granted_hosts("datasources:hello_crossref", ("api.crossref.org",)))
    with pytest.raises(HttpDenied):
        http.get("https://evil.example.net/x")
    with pytest.raises(HttpDenied):
        HostHttp(("api.crossref.org",), resolver=lambda h: ["127.0.0.1"]).get("https://api.crossref.org/works")
    with pytest.raises(HttpDenied):
        http.get("http://api.crossref.org/works")  # https only


def test_dataset_build_and_plugin_template_needs_owner_review(
    hclient: Client, conn: sqlite3.Connection, hello: Any, settings: Settings
) -> None:
    t = hclient.post("/api/v1/tasks", json={"kind": "dataset.build", "payload": {"provider": "hello_synthetic"}}).json()
    drain(conn, hello, max_tasks=1)
    t = hclient.get(f"/api/v1/tasks/{t['id']}").json()
    assert t["state"] == "succeeded" and t["result"]["upstream_checksum"] == "unverified"
    tpl = next(x for x in hclient.get("/api/v1/templates").json() if x["id"] == "hello_centroid")
    assert tpl["status"] == "unreviewed"  # a plugin cannot approve its own template
    import json

    (settings.config_dir / "REVIEWED.json").write_text(json.dumps({"hello_centroid": tpl["hash"]}))
    tpl = next(x for x in hclient.get("/api/v1/templates").json() if x["id"] == "hello_centroid")
    assert tpl["status"] == "reviewed"


def test_plugin_exporter_through_host(hclient: Client, conn: sqlite3.Connection, hello: Any) -> None:
    from .m2_helpers import new_plan, plan_content, run

    pid = new_plan(hclient, plan_content(hclient, None, template="smoke_cpu", params={"n": 60, "steps": 1}))
    rid = run(hclient, conn, hello, pid, stage="smoke")["run"]["id"]
    res = hclient.post("/api/v1/exports/hello_plaintext", json={"run_id": rid}).json()
    assert res["status"] == "ok" and res["files"][0]["mime"] == "text/plain" and len(res["files"][0]["sha256"]) == 64


def test_reference_plugin_imports_only_public_surface() -> None:
    import ast
    allowed = ("researchos.contracts", "researchos.sdk", "researchos_plugin_hello")
    for f in (HELLO / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for n in names:
                if n.startswith("researchos"):
                    assert n.startswith(allowed), f"{f.name} imports private core module {n}"


def test_template_importing_outside_its_dir_is_refused(tmp_path: Path) -> None:
    """The reviewed hash covers only the template directory; importing the plugin package would run unreviewed
    code (found in the external har_datasets 0.1.0 bundle). Both conformance and the host gate refuse it."""
    from researchos.sdk.checks import template_import_violations

    (tmp_path / "run.py").write_text("import json\nimport helper\nfrom researchos.sdk import RunContext\n"
                                     "from researchos_plugin_x.models import run\nfrom researchos.services import x\n")
    (tmp_path / "helper.py").write_text("import numpy as np\nfrom . import sibling\n")
    v = template_import_violations(tmp_path, ["numpy"])
    joined = "\n".join(v)
    assert len(v) == 3 and all(s in joined for s in ("researchos_plugin_x.models", "researchos.services", "'.'"))
    (tmp_path / "run.py").write_text("import json\nimport helper\nfrom researchos.sdk import RunContext\n")
    (tmp_path / "helper.py").write_text("import numpy as np\n")
    assert template_import_violations(tmp_path, ["numpy"]) == []


def test_host_gate_blocks_non_self_contained_template(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from researchos.services.execution.gates import template_gate

    (tmp_path / "run.py").write_text("from researchos_plugin_x.models import run\n")
    tpl = SimpleNamespace(path=tmp_path, hash="h", status="reviewed", manifest=SimpleNamespace(id="x", requires=()))
    g = template_gate(tpl, {"content": {"template": {"id": "x", "hash": "h"}}})  # type: ignore[arg-type]
    assert g is not None and g[0] == "template_not_self_contained" and g[2] == "blocked"
