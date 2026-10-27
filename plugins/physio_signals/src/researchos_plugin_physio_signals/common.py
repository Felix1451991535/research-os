"""Shared provider helpers: patient-level split specs (host format), leakage checks, table writing, descriptors."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from researchos.contracts.datasources import DatasetBuildContext, DatasetDescriptor, DatasetFile

from .fetch import Upstream

SPLITS = ("train", "search_val", "confirm_val", "test")
TABLE = "table.npz"


class LeakageError(RuntimeError):
    """A subject/patient would appear in more than one split."""


class BuildCancelled(RuntimeError):
    """The host requested cancellation."""


def check_cancel(ctx: DatasetBuildContext) -> None:
    if ctx.cancel_requested():
        raise BuildCancelled("dataset build cancelled by request")


def check_disjoint(assign: Mapping[str, Iterable[str]]) -> None:
    seen: dict[str, str] = {}
    for split, groups in assign.items():
        for g in groups:
            if g in seen and seen[g] != split:
                raise LeakageError(f"group {g!r} is assigned to both {seen[g]!r} and {split!r}")
            seen[g] = split


def split_spec(assign: Mapping[str, Iterable[str]], row_groups: Sequence[str]) -> dict[str, Any]:
    """Host split spec {"kind": "groups", "assign": {...}}. Every row's group must be in exactly one split."""
    clean = {k: sorted(set(v)) for k, v in assign.items()}
    check_disjoint(clean)
    covered = {g for v in clean.values() for g in v}
    missing = sorted(set(row_groups) - covered)
    if missing:
        raise LeakageError(f"groups without a split assignment: {missing[:5]}")
    return {"kind": "groups", "assign": clean}


def seeded_partition(groups: Iterable[str], fractions: Mapping[str, float], seed: int) -> dict[str, list[str]]:
    """Patient-level partition: shuffle sorted groups with `seed`; each named split gets round(f*n) groups
    (train keeps at least one); the rest is train. Deterministic for a given (groups, fractions, seed)."""
    pool = sorted(set(groups))
    random.Random(seed).shuffle(pool)
    out: dict[str, list[str]] = {"train": []}
    i = 0
    for name, frac in fractions.items():
        k = min(int(round(frac * len(pool))), max(0, len(pool) - 1 - i))
        out[name] = sorted(pool[i : i + k])
        i += k
    out["train"] = sorted(pool[i:])
    return out


@dataclass
class Table:
    """Rows accumulated during a build. X rows must share one shape; ids must be unique. Extra per-row values are
    scalars (stored as float64) or arrays of one shape per key (stacked, dtype kept)."""

    X: list[np.ndarray[Any, Any]] = field(default_factory=list)
    y: list[int] = field(default_factory=list)
    group: list[str] = field(default_factory=list)
    sample_id: list[str] = field(default_factory=list)
    extra: dict[str, list[Any]] = field(default_factory=dict)

    def add(
        self, x: np.ndarray[Any, Any], y: int, group: str, sample_id: str, **extra: float | np.ndarray[Any, Any]
    ) -> None:
        self.X.append(x)
        self.y.append(y)
        self.group.append(group)
        self.sample_id.append(sample_id)
        for k, v in extra.items():
            self.extra.setdefault(k, []).append(v)

    def write(self, out_dir: Path) -> DatasetFile:
        if not self.y:
            raise ValueError("no samples were extracted; check the selected records and settings")
        if len(set(self.sample_id)) != len(self.sample_id):
            raise ValueError("duplicate sample_id values")
        arrays: dict[str, Any] = {
            "X": np.stack(self.X).astype(np.float32),
            "y": np.asarray(self.y, dtype=np.int64),
            "group": np.asarray(self.group, dtype=str),
            "sample_id": np.asarray(self.sample_id, dtype=str),
        }
        arrays.update(
            {
                k: np.stack(v) if isinstance(v[0], np.ndarray) else np.asarray(v, np.float64)
                for k, v in self.extra.items()
            }
        )
        path = out_dir / TABLE
        np.savez(path, **arrays)
        return DatasetFile(TABLE, hashlib.sha256(path.read_bytes()).hexdigest())


def class_counts(y: Sequence[int], label_map: Mapping[int, str]) -> dict[str, int]:
    return {label_map[k]: int(sum(1 for v in y if v == k)) for k in sorted(label_map)}


OPEN_LICENSES = ("ODC-By-1.0", "ODbL-1.0", "CC-BY-4.0", "CC0-1.0")


def egress_for(license_id: str) -> str:
    """Open-access licences may reach cloud models; anything else (e.g. the PhysioNet Credentialed Health Data
    License, an owner override) stays local_only (core 1.7 egress gate, ADR-159)."""
    return "cloud_ok" if license_id in OPEN_LICENSES else "local_only"


def descriptor(
    name: str,
    version: str,
    up: Upstream,
    lic: tuple[str, str],
    table: Table,
    out_dir: Path,
    extra: dict[str, Any],
    *,
    overlapping_windows: bool = False,
) -> DatasetDescriptor:
    """`lic` comes from Upstream.license(), resolved before any bulk download so a build fails fast.
    `overlapping_windows` must be True when windows of one record can share samples (e.g. beat-centred windows)."""
    lic_id, lic_src = lic
    file = table.write(out_dir)
    expected, observed, details = up.checksum_summary()
    return DatasetDescriptor(
        name=name,
        version=version,
        source=up.base,
        license=lic_id,
        group_key="subject",
        table=TABLE,
        files=(file,),
        overlapping_windows=overlapping_windows,
        egress=egress_for(lic_id),
        expected_source_sha256=expected,
        observed_source_sha256=observed,
        extra={**extra, "license_source": lic_src, "source_checksums": details, "n_samples": len(table.y)},
    )
