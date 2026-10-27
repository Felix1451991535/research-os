"""Conformance for ToolBinding: consistent risk manifest, args match schema, output bounded."""

from __future__ import annotations

from typing import Any

import jsonschema

from ...contracts.gateway import ToolBinding
from ..testing import FakeToolContext


class ToolConformance:
    def make_binding(self) -> ToolBinding:
        raise NotImplementedError

    def sample_args(self) -> dict[str, Any]:
        raise NotImplementedError

    def make_context(self) -> FakeToolContext:
        return FakeToolContext()

    def test_manifest_consistent(self) -> None:
        s = self.make_binding().spec
        assert s.name and s.description and s.params_schema.get("type") == "object"
        assert not (s.read_only and s.destructive), "a read-only tool cannot be destructive"
        assert not s.read_only or s.side_effect_scope == "none", "read-only tools have no side-effect scope"
        assert s.timeout_s > 0 and s.max_output_bytes > 0

    def test_runs_on_valid_args_with_bounded_output(self) -> None:
        b = self.make_binding()
        jsonschema.validate(self.sample_args(), b.spec.params_schema)
        out = b.impl(self.sample_args(), self.make_context())
        assert isinstance(out, str) and len(out.encode()) <= b.spec.max_output_bytes
