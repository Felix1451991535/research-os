"""Trust levels (ARCHITECTURE §5.2). Ordered: failed < unverified < machine_checked < human_checked."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from enum import StrEnum

_ORDER = ["failed", "unverified", "machine_checked", "human_checked"]


class TrustStatus(StrEnum):
    FAILED = "failed"
    UNVERIFIED = "unverified"
    MACHINE_CHECKED = "machine_checked"
    HUMAN_CHECKED = "human_checked"

    @property
    def rank(self) -> int:
        return _ORDER.index(self.value)


def composition(statuses: Iterable[TrustStatus]) -> dict[str, int]:
    """Derived outputs show a composition, not a single minimum."""
    c = Counter(s.value for s in statuses)
    return {k: c.get(k, 0) for k in _ORDER}


def blocks(statuses: Iterable[TrustStatus]) -> bool:
    """Only FAILED inputs block downstream use."""
    return any(s is TrustStatus.FAILED for s in statuses)
