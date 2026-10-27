"""Explicit plugin loading from Python entry points (no string __import__ of internal modules).

Each entry point resolves to an object of the type its group expects (e.g. TaskKindSpec, ApprovalSubjectSpec,
DocKindSpec, fastapi.APIRouter) or a zero-arg callable returning a list of them. Plugins import only
researchos.contracts / researchos.sdk; installing or enabling a plugin requires approval (K3).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from ..contracts import CORE_VERSION
from ..contracts.plugins import ENTRY_POINT_GROUPS, PluginManifest

log = logging.getLogger("researchos.plugins")


@dataclass
class Loaded:
    by_group: dict[str, list[Any]] = field(default_factory=dict)
    owners: dict[int, PluginManifest] = field(default_factory=dict)  # id(object) -> manifest of its plugin
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _expand(obj: Any) -> list[Any]:
    """Entry point -> object(s): an instance is used as is; a class or zero-arg factory is called once.
    Routers (have .routes), manifests and other data objects are never called."""
    if isinstance(obj, type) or callable(obj) and not any(hasattr(obj, a) for a in ("routes", "spec", "model_dump")):
        obj = obj()
    return list(obj) if isinstance(obj, list | tuple) else [obj]


def _dist(ep: Any) -> str:
    d = getattr(ep, "dist", None)
    return str(getattr(d, "name", "")).lower().replace("-", "_") if d else ""


def load_manifests(source: Callable[[str], list[Any]] | None = None) -> dict[str, PluginManifest]:
    """Each plugin distribution declares one `researchos.plugins` entry point -> PluginManifest."""
    out: dict[str, PluginManifest] = {}
    for ep in (source("researchos.plugins") if source else entry_points(group="researchos.plugins")):
        try:
            m = ep.load()
            out[_dist(ep)] = m if isinstance(m, PluginManifest) else PluginManifest.model_validate(m)
        except Exception as e:  # invalid manifest -> plugin refused below
            log.error("invalid plugin manifest %s: %s", getattr(ep, "name", "?"), e)
    return out


def compatible(m: PluginManifest) -> bool:
    try:
        return CORE_VERSION in SpecifierSet(m.core_contract)
    except InvalidSpecifier:
        return False


def load(groups: tuple[str, ...] = ENTRY_POINT_GROUPS,
         loader: Callable[[str], list[Any]] | None = None) -> Loaded:
    """Entries are accepted only from distributions with a valid manifest targeting this core version."""
    out = Loaded()
    manifests = load_manifests(loader)
    for g in groups:
        items: list[Any] = []
        for ep in (loader(g) if loader else entry_points(group=g)):
            m = manifests.get(_dist(ep))
            if m is None or not compatible(m):
                msg = f"{g}:{getattr(ep, 'name', '?')}: refused (missing or incompatible plugin manifest; " \
                      f"core {CORE_VERSION})"
                log.error(msg)
                out.errors.append(msg)
                continue
            try:
                objs = _expand(ep.load())
                items += objs
                out.owners.update({id(o): m for o in objs})
                out.sources.append(f"{m.name}@{m.version} {g}:{ep.name}")
            except Exception as e:  # a broken plugin must not take the core down
                msg = f"{g}:{getattr(ep, 'name', '?')}: {e}"
                log.error("plugin load failed %s", msg)
                out.errors.append(msg)
        out.by_group[g] = items
    return out
