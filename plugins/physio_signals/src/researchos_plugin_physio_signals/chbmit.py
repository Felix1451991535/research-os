"""physio_chbmit: CHB-MIT Scalp EEG (PhysioNet chbmit 1.0.0), subset build of fixed-length windows.

Subset-limited by design (documented): only the selected cases are downloaded (EDF files are ~40 MB each, fetched
in Range chunks), by default only files with seizures; every ictal window is kept and background windows are
evenly sub-sampled per file (max_background_windows_per_record). Labels come from the per-case summary files:
1 = window overlaps an annotated seizure, 0 = otherwise. Case chb21 was recorded from the same subject as chb01
(CHB-MIT description), so both map to one leakage group. There is no official split: subjects are partitioned
with a seeded patient-level assignment.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
from researchos.contracts.datasources import DatasetBuildContext, DatasetDescriptor, DataSourceSpec

from . import edf
from .common import Table, check_cancel, class_counts, descriptor, seeded_partition, split_spec
from .fetch import HOST, Upstream

BASE = "https://physionet.org/files/chbmit/1.0.0/"
VERSION = "1.0.0"
SAME_SUBJECT = {"chb21": "chb01"}
DEFAULT_CHANNELS = (
    "FP1-F7",
    "F7-T7",
    "T7-P7",
    "P7-O1",
    "FP1-F3",
    "F3-C3",
    "C3-P3",
    "P3-O1",
    "FP2-F4",
    "F4-C4",
    "C4-P4",
    "P4-O2",
    "FP2-F8",
    "F8-T8",
    "T8-P8",
    "P8-O2",
    "FZ-CZ",
    "CZ-PZ",
)

SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["subjects"],
    "properties": {
        "subjects": {
            "type": "array",
            "items": {"type": "string", "pattern": "^chb[0-9]{2}$"},
            "minItems": 1,
            "uniqueItems": True,
            "description": "CHB-MIT cases to download, e.g. ['chb01', 'chb02']",
        },
        "seizure_records_only": {"type": "boolean", "description": "only EDF files with >= 1 seizure (default)"},
        "window_s": {"type": "number", "minimum": 1, "maximum": 30, "description": "window length (default 4)"},
        "max_background_windows_per_record": {
            "type": "integer",
            "minimum": 0,
            "description": "evenly spaced non-ictal windows per file (60)",
        },
        "channels": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
        "seed": {"type": "integer", "minimum": 0},
        "val_fraction": {"type": "number", "minimum": 0, "maximum": 0.4},
        "test_fraction": {"type": "number", "minimum": 0, "maximum": 0.5},
        "license_override": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


class SummaryError(ValueError):
    """Summary file inconsistent (e.g. stated seizure count differs from the listed intervals)."""


@dataclass(frozen=True)
class FileInfo:
    name: str
    seizures: tuple[tuple[int, int], ...]  # (start_s, end_s)


_START = re.compile(r"Seizure(?:\s+\d+)?\s+Start Time:\s*(\d+)\s*seconds", re.I)
_END = re.compile(r"Seizure(?:\s+\d+)?\s+End Time:\s*(\d+)\s*seconds", re.I)
_COUNT = re.compile(r"Number of Seizures in File:\s*(\d+)", re.I)


def _file_block(block: str) -> FileInfo:
    name = block.split(None, 1)[0].strip()
    count = _COUNT.search(block)
    starts, ends = [int(x) for x in _START.findall(block)], [int(x) for x in _END.findall(block)]
    if count is None or int(count[1]) != len(starts) or len(starts) != len(ends):
        raise SummaryError(f"{name}: seizure count does not match the listed start/end times")
    pairs = tuple(zip(starts, ends, strict=True))
    if any(e <= s for s, e in pairs):
        raise SummaryError(f"{name}: seizure end time not after start time")
    return FileInfo(name, pairs)


def parse_summary(text: str) -> dict[str, FileInfo]:
    blocks = re.split(r"File Name:\s*", text)[1:]
    infos = [_file_block(b) for b in blocks]
    return {i.name: i for i in infos}


def subject_of(case: str) -> str:
    return f"chbmit_{SAME_SUBJECT.get(case, case)}"


def channel_rows(labels: tuple[str, ...], wanted: tuple[str, ...]) -> list[int] | None:
    """Row index per wanted channel (first occurrence; CHB-MIT duplicates appear as '<name>-0'), None if absent."""
    norm = [lab.strip().upper() for lab in labels]
    rows = []
    for ch in wanted:
        hit = next((i for i, lab in enumerate(norm) if lab in (ch.upper(), ch.upper() + "-0")), None)
        if hit is None:
            return None
        rows.append(hit)
    return rows


def window_labels(n_windows: int, win: int, fs: float, seizures: tuple[tuple[int, int], ...]) -> np.ndarray[Any, Any]:
    starts = np.arange(n_windows) * win
    lab = np.zeros(n_windows, dtype=np.int64)
    for s, e in seizures:
        lab[(starts < e * fs) & (starts + win > s * fs)] = 1
    return lab


def pick_windows(labels: np.ndarray[Any, Any], max_bg: int) -> list[int]:
    ictal = np.flatnonzero(labels == 1).tolist()
    bg = np.flatnonzero(labels == 0)
    if max_bg and len(bg):
        bg = bg[np.unique(np.linspace(0, len(bg) - 1, min(max_bg, len(bg))).round().astype(int))]
    else:
        bg = bg[:0]
    return sorted(ictal + bg.tolist())


class ChbMitProvider:
    spec = DataSourceSpec(
        "physio_chbmit",
        "0.1.0",
        "dataset",
        "CHB-MIT scalp EEG (PhysioNet chbmit 1.0.0), subset build: ictal vs background windows per subject",
        hosts=(HOST,),
        settings_schema=SETTINGS_SCHEMA,
    )

    def _files(self, up: Upstream, settings: Mapping[str, Any]) -> list[tuple[str, FileInfo]]:
        subjects = [str(s) for s in settings.get("subjects") or ()]
        if not subjects:
            raise ValueError("physio_chbmit needs settings.subjects (e.g. ['chb01']); no default subset is guessed")
        listed = set(up.get_text("RECORDS").split())
        out: list[tuple[str, FileInfo]] = []
        for case in sorted(subjects):
            summary = parse_summary(up.get_text(f"{case}/{case}-summary.txt"))
            for name, info in sorted(summary.items()):
                if f"{case}/{name}" in listed and (info.seizures or not settings.get("seizure_records_only", True)):
                    out.append((case, info))
        if not out:
            raise ValueError(f"no matching EDF files for subjects {subjects}")
        return out

    def _add_record(
        self,
        case: str,
        info: FileInfo,
        raw: bytes,
        settings: Mapping[str, Any],
        table: Table,
        skipped: dict[str, str],
        units: set[str],
    ) -> float:
        hdr, sig = edf.read_edf(raw)
        fs = edf.sampling_rate(hdr)
        idx = edf.signal_indices(hdr)
        rows = channel_rows(tuple(hdr.labels[i] for i in idx), tuple(settings.get("channels") or DEFAULT_CHANNELS))
        if rows is None:
            skipped[info.name] = "missing one of the requested channels"
            return fs
        units.update(hdr.phys_dim[idx[r]] or "unstated" for r in rows)
        win = int(round(float(settings.get("window_s", 4)) * fs))
        lab = window_labels(sig.shape[1] // win, win, fs, info.seizures)
        for k in pick_windows(lab, int(settings.get("max_background_windows_per_record", 60))):
            table.add(
                sig[rows, k * win : (k + 1) * win],
                int(lab[k]),
                subject_of(case),
                f"chbmit/{info.name}/{k}",
                sampling_rate_hz=fs,  # per row, for templates (ADR-177)
            )
        return fs

    def build(self, ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> DatasetDescriptor:
        up = Upstream(ctx.http, BASE)
        up.load_checksums()
        lic = up.license(settings)
        files = self._files(up, settings)
        table, fs_seen = Table(), set()
        skipped: dict[str, str] = {}
        units: set[str] = set()
        for i, (case, info) in enumerate(files):
            check_cancel(ctx)
            raw = up.get(f"{case}/{info.name}")
            fs_seen.add(self._add_record(case, info, raw, settings, table, skipped, units))
            ctx.progress("records", i + 1, len(files))
        if len(fs_seen) != 1:
            raise edf.EdfError(f"files have different sampling rates {sorted(fs_seen)}")
        val, test = float(settings.get("val_fraction", 0.15)), float(settings.get("test_fraction", 0.2))
        assign = seeded_partition(
            table.group, {"test": test, "search_val": val, "confirm_val": val}, int(settings.get("seed", 0))
        )
        label_map = {0: "background", 1: "ictal"}
        extra = {
            "dataset": "CHB-MIT Scalp EEG Database",
            "upstream_version": VERSION,
            "signal_type": "EEG",
            "sampling_rate_hz": fs_seen.pop(),
            "channels": list(settings.get("channels") or DEFAULT_CHANNELS),
            "units": sorted(units),
            "window_s": settings.get("window_s", 4),
            "x_layout": "X[N, channels, window_samples]; sampling_rate_hz[N]",
            "label_map": {str(k): v for k, v in label_map.items()},
            "class_counts": class_counts(table.y, label_map),
            "records": [f"{c}/{f.name}" for c, f in files],
            "skipped_records": skipped,
            "subset_limited": True,
            "background_sampling": "evenly spaced, max_background_windows_per_record per file",
            "split": split_spec(assign, table.group),
            "split_warnings": [f"split {k!r} is empty" for k, v in assign.items() if not v],
            "split_source": "no official split; seeded patient-level partition (chb21 merged with chb01)",
        }
        return descriptor("physio_chbmit", VERSION, up, lic, table, ctx.out_dir, extra)
