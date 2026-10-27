"""Privacy ports (core 1.6): extension points for future clinical sources, with NO clinical features in core.

- Import port: a clinical source (FHIR / HL7 / PACS / local exports) is a `DatasetProvider` (contracts.datasources)
  using `files:read` or `network:<host>` capabilities; nothing new is needed for import itself.
- PHI port: `PhiInspector` reports which identifying attributes are present, as keywords and counts only.
- Identity port: `Pseudonymizer` maps source identifiers to pseudonyms and states whether they link across builds.
Implementations live in plugins (reference: medimg_dicom). See ADR-158 in docs/DECISIONS.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class PhiFindings:
    """Never contains values (keys come from the inspector's closed vocabulary, never from the data): attribute
    keyword -> records where it is non-empty; `other` = further detected risk signals (private tags, overlays,
    burned-in annotation); `uncertain` = risks the inspection could not rule out (e.g. burned-in status unknown on a
    modality that often carries burned-in text). `coverage` states what was inspected, e.g. "subset of PS3.15
    Annex E basic profile, nested sequences included". Callers must treat uncertain as not clear (review #44)."""

    records: int
    attributes: Mapping[str, int] = field(default_factory=dict)
    other: Mapping[str, int] = field(default_factory=dict)
    reference: str = ""
    uncertain: Mapping[str, int] = field(default_factory=dict)
    coverage: str = ""

    def found(self) -> bool:
        return any(self.attributes.values()) or any(self.other.values())

    def clear(self) -> bool:
        """Nothing found AND nothing left uncertain. A clear result is only as complete as `coverage`."""
        return not self.found() and not any(self.uncertain.values())


class PhiInspector(Protocol):
    def inspect(self, records: Iterable[Any]) -> PhiFindings: ...


class Pseudonymizer(Protocol):
    """Key lifetime = the instance; one instance per build (review #46). `linkable` False means no INTENDED identifier
    stability across instances (the key is never stored); it is not anonymity: the same instance is stable for its
    whole life, and the underlying data (e.g. pixels, signals) can still re-identify or link records. True: a
    host-held key links the same person across builds (longitudinal studies) and is itself identifying material
    (needs its own ADR on key custody). Namespaces and values are arbitrary strings; implementations must encode
    them unambiguously (no separator collisions, review #45)."""

    linkable: bool

    def pseudonym(self, namespace: str, value: str) -> str: ...
