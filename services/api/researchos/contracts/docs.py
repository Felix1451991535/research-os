"""K8 Content contract: authored document kinds."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DocKindSpec:
    name: str
    description: str
    block_schema: dict[str, Any] = field(default_factory=dict)  # JSON Schema of the body; {} = free text
