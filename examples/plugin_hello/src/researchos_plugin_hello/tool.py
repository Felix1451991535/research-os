"""Tool binding: read-only, no side effects, bounded output."""

from __future__ import annotations

from typing import Any

from researchos.contracts.gateway import ToolBinding, ToolContext, ToolSpec


def _count(args: dict[str, Any], ctx: ToolContext) -> str:
    return str(len(str(args["text"]).split()))


word_count = ToolBinding(
    ToolSpec(
        "hello_word_count",
        "count words in a text",
        {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string", "maxLength": 100000}},
            "additionalProperties": False,
        },
        read_only=True,
        destructive=False,
        side_effect_scope="none",
        max_output_bytes=32,
    ),
    _count,
)
