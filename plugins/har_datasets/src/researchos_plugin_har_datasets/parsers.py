"""UCI supplied windows and WISDM v1 raw accelerometer segmentation."""

from __future__ import annotations

import hashlib
import io
import re
from typing import Any

import numpy as np
import numpy.typing as npt

from .archive import member

Array = npt.NDArray[Any]
CHANNELS = tuple(f"{kind}_{axis}" for kind in ("body_acc", "body_gyro", "total_acc") for axis in "xyz")
ACTIVITIES = ("Walking", "Jogging", "Upstairs", "Downstairs", "Sitting", "Standing")


def validate(arrays: dict[str, Array]) -> None:
    x, y, groups, ids = (arrays[k] for k in ("X", "y", "group", "sample_id"))
    if x.ndim != 3 or not len(x) or not np.isfinite(x).all():
        raise ValueError("Expected finite nonempty [N,T,C] windows")
    if any(a.ndim != 1 or len(a) != len(x) for a in (y, groups, ids)):
        raise ValueError("Mismatched sample arrays")
    if not np.issubdtype(y.dtype, np.integer) or np.any(y < 0) or len(set(ids.tolist())) != len(x):
        raise ValueError("Invalid labels or duplicate sample IDs")


def uci_official_test_subjects(files: dict[str, bytes]) -> list[str]:
    """The official UCI HAR test partition (by subject), kept so results stay comparable with the literature."""
    g = np.loadtxt(io.BytesIO(member(files, "test/subject_test.txt")), dtype=np.int64, ndmin=1)
    return sorted({str(int(v)) for v in g}, key=int)


def uci(files: dict[str, bytes], source_hash: str) -> dict[str, Array]:
    result: dict[str, list[Array]] = {k: [] for k in ("X", "y", "group", "sample_id")}
    for partition in ("train", "test"):
        signals = [
            np.loadtxt(
                io.BytesIO(member(files, f"{partition}/Inertial Signals/{c}_{partition}.txt")),
                dtype=np.float32,
                ndmin=2,
            )
            for c in CHANNELS
        ]
        x = np.stack(signals, axis=2)
        if x.shape[1] != 128:
            raise ValueError("UCI HAR requires 128-sample windows")
        y = np.loadtxt(io.BytesIO(member(files, f"{partition}/y_{partition}.txt")), dtype=np.int64, ndmin=1)
        g = np.loadtxt(io.BytesIO(member(files, f"{partition}/subject_{partition}.txt")), dtype=np.int64, ndmin=1)
        if np.any((y < 1) | (y > 6)) or np.any(g < 1):
            raise ValueError("Invalid UCI label or subject")
        result["X"].append(x)
        result["y"].append(y - 1)
        result["group"].append(g.astype(str))
        result["sample_id"].append(np.array([f"{source_hash}:{partition}:{i}" for i in range(len(y))]))
    arrays = {k: np.concatenate(v) for k, v in result.items()}
    validate(arrays)
    return arrays


MAX_SKIPPED_FRACTION = 0.01


def _row(record: str) -> tuple[str, str, int, list[float]]:
    parts = record.strip().split(",")
    if len(parts) != 6 or parts[1] not in ACTIVITIES or not parts[0].isdigit():
        raise ValueError("Malformed WISDM row or unknown activity")
    stamp, values = int(parts[2]), [float(v) for v in parts[3:]]
    if stamp <= 0 or not np.isfinite(values).all():
        raise ValueError("Invalid WISDM timestamp or sensor value")
    return parts[0], parts[1], stamp, values


def _segments(raw: bytes, max_gap_ns: int) -> tuple[list[list[tuple[str, str, int, list[float]]]], int, int]:
    """Records are ';'-terminated (some lines in the public file hold several records). Malformed records are
    skipped and counted; more than 1% malformed records is an error, never a silent repair."""
    segments: list[list[tuple[str, str, int, list[float]]]] = []
    previous: tuple[str, str, int] | None = None
    skipped = total = 0
    for record in re.split(r"[;\n]", raw.decode("utf-8-sig")):
        if not record.strip():
            continue
        total += 1
        try:
            subject, label, stamp, values = _row(record)
        except ValueError:
            skipped += 1
            previous = None  # never join a window across a dropped record
            continue
        if previous is None or previous[:2] != (subject, label) or not 0 < stamp - previous[2] <= max_gap_ns:
            segments.append([])
        segments[-1].append((subject, label, stamp, values))
        previous = subject, label, stamp
    if total == 0 or skipped > MAX_SKIPPED_FRACTION * total:
        raise ValueError(f"Too many malformed WISDM records: {skipped}/{total}")
    return segments, skipped, total


def wisdm(files: dict[str, bytes], source_hash: str, window: int, max_gap_ns: int) -> dict[str, Array]:
    return wisdm_with_stats(files, source_hash, window, max_gap_ns)[0]


def wisdm_with_stats(
    files: dict[str, bytes], source_hash: str, window: int, max_gap_ns: int
) -> tuple[dict[str, Array], dict[str, int]]:
    if not 2 <= window <= 4096 or max_gap_ns <= 0:
        raise ValueError("Invalid window or timestamp gap")
    segments, skipped, total = _segments(member(files, "WISDM_ar_v1.1_raw.txt"), max_gap_ns)
    x, y, groups, ids = [], [], [], []
    for segment in segments:
        for start in range(0, len(segment) - window + 1, window):
            rows = segment[start : start + window]
            subject, label, stamp, _ = rows[0]
            identity = f"{source_hash}:{subject}:{label}:{stamp}:{rows[-1][2]}"
            x.append([r[3] for r in rows])
            y.append(ACTIVITIES.index(label))
            groups.append(subject)
            ids.append(hashlib.sha256(identity.encode()).hexdigest())
    arrays: dict[str, Array] = {
        "X": np.asarray(x, dtype=np.float32),
        "y": np.asarray(y, dtype=np.int64),
        "group": np.asarray(groups),
        "sample_id": np.asarray(ids),
    }
    validate(arrays)
    return arrays, {"records": total, "skipped_malformed_records": skipped}


def subject_split(groups: Array, seed: int) -> dict[str, list[str]]:
    subjects = sorted(set(str(g) for g in groups))
    if len(subjects) < 3:
        raise ValueError("At least three subjects needed for nonempty train/validation/test splits")
    rng = np.random.default_rng(seed)
    rng.shuffle(subjects)
    ntest = max(1, len(subjects) // 5)
    nval = max(1, len(subjects) // 5)
    return {
        "train": sorted(subjects[ntest + nval :]),
        "search_val": sorted(subjects[ntest : ntest + nval]),
        "test": sorted(subjects[:ntest]),
    }
