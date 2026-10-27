"""Task-kind registry. Built once at startup (core kinds + plugin entry points), then frozen."""
from __future__ import annotations

from collections.abc import Iterable

from ...contracts.tasks import TaskKindSpec


class KindRegistry:
    def __init__(self, specs: Iterable[TaskKindSpec] = ()) -> None:
        self._specs: dict[str, TaskKindSpec] = {}
        self._frozen = False
        for s in specs:
            self.register(s)

    def register(self, spec: TaskKindSpec) -> None:
        if self._frozen:
            raise RuntimeError("registry is frozen after startup")
        if spec.name in self._specs:
            raise ValueError(f"duplicate task kind {spec.name}")
        self._specs[spec.name] = spec

    def freeze(self) -> KindRegistry:
        self._frozen = True
        return self

    def get(self, name: str) -> TaskKindSpec | None:
        return self._specs.get(name)

    def all(self) -> list[TaskKindSpec]:
        return list(self._specs.values())
