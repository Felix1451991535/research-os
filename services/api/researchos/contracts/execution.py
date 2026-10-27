"""K5 Execution contract: templates, run bundles, executors (local process now; GPU/remote later)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

STAGE_SPLITS: dict[str, tuple[str, ...]] = {
    # Which materialized splits a stage may see. "test" only for final_eval (after freeze + approval).
    "trial": ("train", "search_val"),
    "confirm": ("train", "confirm_val"),
    "final_eval": ("train", "test"),
    "smoke": (),
}


class Stage(StrEnum):
    TRIAL = "trial"
    CONFIRM = "confirm"
    FINAL_EVAL = "final_eval"
    SMOKE = "smoke"


@dataclass(frozen=True)
class TemplateManifest:
    """templates/<id>/template.json"""

    id: str
    version: str
    entry: str  # python file relative to the template dir
    description: str
    params_schema: dict[str, Any]
    resource_class: str = "cpu"  # cpu | gpu
    metrics: tuple[dict[str, Any], ...] = ()  # declared headline metrics {name, split, verification, unit, range}
    task: dict[str, Any] = field(default_factory=dict)  # e.g. {"type": "classification", "n_classes": 6}
    requires: tuple[str, ...] = ()  # python modules needed (honest "missing dependency" if absent)


@dataclass(frozen=True)
class RunBundle:
    """Everything an executor may touch: a materialized directory + its manifest. Never raw dataset paths."""

    run_id: str
    run_dir: Path
    entry: Path
    env: dict[str, str]
    limits: dict[str, Any]  # max_minutes, memory_mb, stall_s
    device_label: str


@dataclass
class ExecStatus:
    alive: bool
    exit_code: int | None
    rss_mb: float | None = None


class Executor(Protocol):
    name: str

    def start(self, bundle: RunBundle) -> Any: ...

    def poll(self, handle: Any) -> ExecStatus: ...

    def request_cancel(self, handle: Any) -> None: ...

    def kill(self, handle: Any) -> bool: ...


@dataclass(frozen=True)
class TemplatePackage:
    """Entry-point group `researchos.templates`: a directory containing template.json + code. Plugin templates are
    UNREVIEWED until the owner records their hash in <data>/config/REVIEWED.json (scripts/review_template.py);
    a REVIEWED.json shipped inside a plugin is ignored (no self-approval)."""
    root: Path
