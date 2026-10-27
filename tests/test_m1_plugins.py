"""Plugin manifests are validated before any entry is loaded (M0 review)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from researchos.contracts.plugins import PluginManifest
from researchos.contracts.tasks import TaskKindSpec
from researchos.services import plugins


@dataclass
class FakeDist:
    name: str


@dataclass
class FakeEP:
    name: str
    obj: Any
    dist: FakeDist
    value: str = "x:y"

    def load(self) -> Any:
        return self.obj


def make_loader(core: str) -> Any:
    spec = TaskKindSpec("demo.kind", "demo")
    eps = {"researchos.plugins": [FakeEP("demo", PluginManifest(name="demo", version="0.1", core_contract=core),
                                         FakeDist("demo"))],
           "researchos.task_kinds": [FakeEP("k", spec, FakeDist("demo")), FakeEP("rogue", spec, FakeDist("rogue"))]}
    return lambda g: eps.get(g, [])


def test_compatible_plugin_loaded_rogue_refused() -> None:
    out = plugins.load(("researchos.task_kinds",), make_loader(">=1.0,<2"))
    assert [k.name for k in out.by_group["researchos.task_kinds"]] == ["demo.kind"]
    assert any("rogue" in e and "refused" in e for e in out.errors)


def test_incompatible_core_version_refused() -> None:
    out = plugins.load(("researchos.task_kinds",), make_loader(">=2.0"))
    assert out.by_group["researchos.task_kinds"] == [] and len(out.errors) == 2
