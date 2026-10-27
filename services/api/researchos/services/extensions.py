"""Host registries for plugin extension points (contracts: datasources, importers, exporters, tools, templates).
Classification is structural (protocol members), so plugins depend only on researchos.contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..contracts.execution import TemplatePackage
from ..contracts.gateway import ToolBinding
from ..contracts.plugins import PluginManifest
from .content.md_exporter import MarkdownExporter
from .plugins import Loaded


@dataclass
class Extensions:
    sources: dict[str, Any] = field(default_factory=dict)            # BibliographicSource
    dataset_providers: dict[str, Any] = field(default_factory=dict)  # DatasetProvider
    oa_resolvers: dict[str, Any] = field(default_factory=dict)       # OaResolver (core 1.4)
    importers: dict[str, Any] = field(default_factory=dict)          # Importer
    file_importers: dict[str, Any] = field(default_factory=dict)     # FileImporter
    exporters: dict[str, Any] = field(default_factory=dict)          # Exporter
    tools: dict[str, ToolBinding] = field(default_factory=dict)
    template_roots: list[Path] = field(default_factory=list)
    owners: dict[str, PluginManifest] = field(default_factory=dict)  # "<group>:<name>" -> manifest
    rejected: list[str] = field(default_factory=list)

    def granted_hosts(self, key: str, declared: tuple[str, ...]) -> tuple[str, ...]:
        """Hosts = spec.hosts ∩ manifest capabilities `network:<host>` (a plugin cannot grant itself more)."""
        m = self.owners.get(key)
        caps = {c.split(":", 1)[1] for c in (m.capabilities if m else []) if c.startswith("network:")}
        return tuple(h for h in declared if h in caps)

    def has_capability(self, key: str, capability: str) -> bool:
        """Whether the plugin owning `key` declares `capability` in its manifest (installing it = owner trust)."""
        m = self.owners.get(key)
        return bool(m and capability in m.capabilities)

    def summary(self) -> dict[str, list[str]]:
        return {"sources": sorted(self.sources), "dataset_providers": sorted(self.dataset_providers),
                "oa_resolvers": sorted(self.oa_resolvers),
                "citation_graph": sorted(n for n, s in self.sources.items() if hasattr(s, "cited_by")),
                "retraction": sorted(n for n, s in self.sources.items() if hasattr(s, "retraction_status")),
                "importers": sorted(self.importers), "file_importers": sorted(self.file_importers),
                "exporters": sorted(self.exporters), "tools": sorted(self.tools),
                "templates": [str(p) for p in self.template_roots]}


def _put(ext: Extensions, table: dict[str, Any], group: str, obj: Any, loaded: Loaded) -> None:
    name = obj.spec.name
    if name in table:
        ext.rejected.append(f"{group}:{name}: duplicate name")
        return
    table[name] = obj
    if id(obj) in loaded.owners:
        ext.owners[f"{group}:{name}"] = loaded.owners[id(obj)]


def _datasource(ext: Extensions, obj: Any, loaded: Loaded) -> None:
    kind = getattr(getattr(obj, "spec", None), "kind", None)
    if kind == "bibliographic" and hasattr(obj, "search"):
        _put(ext, ext.sources, "datasources", obj, loaded)
    elif kind == "dataset" and hasattr(obj, "build"):
        _put(ext, ext.dataset_providers, "datasources", obj, loaded)
    elif kind == "oa_resolver" and hasattr(obj, "resolve"):
        _put(ext, ext.oa_resolvers, "datasources", obj, loaded)
    else:
        ext.rejected.append(f"datasources: {obj!r} does not implement a DataSource protocol")


def _importer(ext: Extensions, obj: Any, loaded: Loaded) -> None:
    if hasattr(obj, "import_records"):
        _put(ext, ext.importers, "importers", obj, loaded)
    elif hasattr(obj, "parse_file"):
        _put(ext, ext.file_importers, "importers", obj, loaded)
    else:
        ext.rejected.append(f"importers: {obj!r} does not implement Importer/FileImporter")


def _exporter(ext: Extensions, obj: Any, loaded: Loaded) -> None:
    if hasattr(obj, "export") and hasattr(getattr(obj, "spec", None), "mime_types"):
        _put(ext, ext.exporters, "exporters", obj, loaded)
    else:
        ext.rejected.append(f"exporters: {obj!r} does not implement Exporter")


def _tool(ext: Extensions, obj: Any, loaded: Loaded) -> None:
    if not isinstance(obj, ToolBinding) or obj.spec.name in ext.tools:
        ext.rejected.append(f"tools: {obj!r} is not a ToolBinding (or duplicate)")
        return
    ext.tools[obj.spec.name] = obj
    if id(obj) in loaded.owners:
        ext.owners[f"tools:{obj.spec.name}"] = loaded.owners[id(obj)]


def _template(ext: Extensions, obj: Any, loaded: Loaded) -> None:
    if isinstance(obj, TemplatePackage) and (obj.root / "template.json").is_file():
        ext.template_roots.append(obj.root)
    else:
        ext.rejected.append(f"templates: {obj!r} is not a TemplatePackage with template.json")


HANDLERS = {"researchos.datasources": _datasource, "researchos.importers": _importer,
            "researchos.exporters": _exporter, "researchos.tools": _tool, "researchos.templates": _template}


def build_extensions(loaded: Loaded) -> Extensions:
    ext = Extensions(exporters={"markdown": MarkdownExporter()})
    for group, handler in HANDLERS.items():
        for obj in loaded.by_group.get(group, []):
            handler(ext, obj, loaded)
    return ext
