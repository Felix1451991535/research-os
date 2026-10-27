"""K9 Verification contract: detectors are read-only over a run view; their only output is HealthSignal."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Severity = Literal["info", "warn", "fail"]


@dataclass(frozen=True)
class HealthSignal:
    detector: str  # "nonfinite@1"
    severity: Severity
    rule: str
    value: dict[str, Any] = field(default_factory=dict)
    selector: dict[str, Any] = field(default_factory=dict)  # e.g. {"metric": "train_loss", "step": [3, 3]}


class RunView(Protocol):
    """Read-only accessor handed to detectors."""

    def metric_records(self) -> list[dict[str, Any]]: ...

    def summary(self) -> list[dict[str, Any]]: ...

    def predictions(self, key: str) -> list[dict[str, str]] | None: ...

    def split_ids(self, split: str) -> set[str] | None: ...

    def accessed_splits(self) -> list[str]: ...

    @property
    def manifest(self) -> dict[str, Any]: ...

    @property
    def template(self) -> dict[str, Any]: ...


class Detector(Protocol):
    id: str
    version: int
    streaming: bool  # True: also evaluated on the live metrics tail

    def check(self, view: RunView) -> list[HealthSignal]: ...
