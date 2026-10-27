"""Plugin registration contract (ARCHITECTURE §3)."""
from __future__ import annotations

from pydantic import BaseModel, Field

ENTRY_POINT_GROUPS: tuple[str, ...] = tuple(f"researchos.{g}" for g in (
    "routers", "entity_types", "doc_types", "artifact_types", "datasources", "importers", "parsers",
    "retrievers", "indexers", "providers", "tools", "prompts", "executors", "templates", "search_strategies",
    "designs", "detectors", "verifiers", "analyzers", "checkers", "exporters", "effects", "approval_subjects",
    "triggers", "channels", "settings", "task_kinds"))


class PluginManifest(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    version: str
    core_contract: str = Field(description="semver range of core contracts targeted, e.g. '>=1.0,<2'")
    capabilities: list[str] = Field(default_factory=list)
    settings_schema: dict[str, object] = Field(default_factory=dict)
    migration_namespace: str | None = None
