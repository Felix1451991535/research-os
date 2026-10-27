"""Tool registry with self-describing risk manifests (HARDENING #14, #15). Permission decisions read ONLY the
ToolSpec; they are re-evaluated on the final (possibly hook-mutated) arguments right before execution."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jsonschema

from ...contracts.gateway import ToolError, ToolSpec

ToolImpl = Callable[[dict[str, Any]], str]
ArgHook = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    impl: ToolImpl


class ToolDenied(Exception):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._t: dict[str, Tool] = {}

    def register(self, spec: ToolSpec, impl: ToolImpl) -> None:
        if spec.name in self._t:
            raise ValueError(f"duplicate tool {spec.name}")
        self._t[spec.name] = Tool(spec, impl)

    def get(self, name: str) -> Tool | None:
        return self._t.get(name)

    def names(self) -> list[str]:
        return sorted(self._t)


def permit(spec: ToolSpec, args: dict[str, Any], granted: frozenset[str]) -> None:
    """M1 policy: agents may run read-only, non-destructive tools without side effects; anything else must go
    through K4 Effects + K3 approval (not available to agents directly). Capability needs must be granted."""
    if not spec.read_only or spec.destructive or spec.side_effect_scope not in ("none",):
        raise ToolDenied(f"tool {spec.name} has side effects ({spec.side_effect_scope}); requires an approved effect")
    missing = [n for n in spec.needs if n not in granted]
    if missing:
        raise ToolDenied(f"tool {spec.name} needs capabilities not granted to this run: {missing}")
    jsonschema.validate(args, spec.params_schema)


def execute(tool: Tool, args: dict[str, Any], granted: frozenset[str], hooks: tuple[ArgHook, ...] = ()) -> str:
    permit(tool.spec, args, granted)
    final = args
    for h in hooks:
        final = h(tool.spec.name, final)
    if final != args:
        permit(tool.spec, final, granted)  # input mutated after the decision -> decide again (HARDENING #15)
    out = tool.impl(final)
    size = len(out.encode("utf-8"))
    if size > tool.spec.max_output_bytes:  # rejected, never truncated (a cut JSON document is invalid data)
        raise ToolError("output_too_large", f"output of {size} bytes exceeds {tool.spec.max_output_bytes}; "
                                            "narrow the request")
    return out
