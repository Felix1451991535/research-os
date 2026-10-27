"""physio_mitbih: MIT-BIH Arrhythmia Database (PhysioNet mitdb 1.0.0) as beat windows with AAMI EC57 classes, or
(`unit: "segment"`) as continuous MLII segments with reference R peaks for peak-detection validation and HRV.

Protocol (documented in README): inter-patient split of de Chazal et al. (2004) - DS1 for development, DS2 held out
as test. Paced records 102/104/107/217 are excluded (AAMI practice). Record 202 is excluded from DS2 because records
201 (DS1) and 202 come from the same subject (MIT-BIH directory), which would otherwise leak a subject across
train/test. DS1 subjects are partitioned (seeded, patient-level) into train / search_val / confirm_val.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

import numpy as np
from researchos.contracts.datasources import DatasetBuildContext, DatasetDescriptor, DataSourceSpec

from . import wfdb
from .common import Table, check_cancel, class_counts, descriptor, seeded_partition, split_spec
from .fetch import HOST, Upstream

BASE = "https://physionet.org/files/mitdb/1.0.0/"
VERSION = "1.0.0"
DS1 = (
    "101",
    "106",
    "108",
    "109",
    "112",
    "114",
    "115",
    "116",
    "118",
    "119",
    "122",
    "124",
    "201",
    "203",
    "205",
    "207",
    "208",
    "209",
    "215",
    "220",
    "223",
    "230",
)
DS2_STANDARD = (
    "100",
    "103",
    "105",
    "111",
    "113",
    "117",
    "121",
    "123",
    "200",
    "202",
    "210",
    "212",
    "213",
    "214",
    "219",
    "221",
    "222",
    "228",
    "231",
    "232",
    "233",
    "234",
)
EXCLUDED = {
    "102": "paced record (AAMI EC57 practice)",
    "104": "paced record (AAMI EC57 practice)",
    "107": "paced record (AAMI EC57 practice)",
    "217": "paced record (AAMI EC57 practice)",
    "202": "same subject as DS1 record 201 (MIT-BIH directory); kept out to avoid train/test subject leakage",
}
DS2 = tuple(r for r in DS2_STANDARD if r not in EXCLUDED)
SAME_SUBJECT = {"202": "201"}
AAMI_CLASSES = ("N", "S", "V", "F", "Q")
AAMI_MAP = {
    "N": "N",
    "L": "N",
    "R": "N",
    "e": "N",
    "j": "N",
    "A": "S",
    "a": "S",
    "J": "S",
    "S": "S",
    "V": "V",
    "E": "V",
    "F": "F",
    "/": "Q",
    "f": "Q",
    "Q": "Q",
}
LEAD = "MLII"
WINDOW_PRE_S, WINDOW_POST_S = 0.25, 0.45
SEGMENT_S = 300.0  # default segment length: the conventional 5 min short-term HRV recording
MAX_BEATS_PER_S = 4  # 240 bpm bounds the reference peak array width; a denser segment is an error, never truncated
ECTOPIC = ("S", "V", "F")
# Every WFDB beat annotation (isqrs): reference R peaks include beats outside the AAMI map (B, n, r, ?, !), which only
# affects peak validation, not labels.
BEAT_SYMBOLS = frozenset(AAMI_MAP) | {"B", "n", "r", "?", "!"}

SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "records": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[0-9]{3}$"},
            "minItems": 1,
            "uniqueItems": True,
            "description": "subset of DS1+DS2 record names (default: all 43)",
        },
        "seed": {"type": "integer", "minimum": 0, "description": "seed of the DS1 patient-level partition"},
        "unit": {
            "enum": ["beat", "segment"],
            "description": "beat windows (default) or continuous segments with reference R peaks",
        },
        "segment_s": {"type": "number", "minimum": 10, "maximum": 1800, "description": "segment length (300)"},
        "val_fraction": {
            "type": "number",
            "minimum": 0,
            "maximum": 0.4,
            "description": "fraction of DS1 subjects for search_val and for confirm_val (each)",
        },
        "license_override": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


def subject_of(record: str) -> str:
    return f"mitdb_{SAME_SUBJECT.get(record, record)}"


def selected_records(settings: Mapping[str, Any], available: list[str]) -> list[str]:
    wanted = [str(r) for r in settings.get("records") or (*DS1, *DS2)]
    bad = [r for r in wanted if r in EXCLUDED]
    if bad:
        raise ValueError(f"records {bad} are excluded: " + "; ".join(f"{r}: {EXCLUDED[r]}" for r in bad))
    unknown = [r for r in wanted if r not in DS1 and r not in DS2]
    missing = [r for r in wanted if r not in available]
    if unknown or missing:
        raise ValueError(f"records not in the DS1/DS2 protocol: {unknown}; not listed upstream: {missing}")
    return sorted(wanted)


def lead_index(header: wfdb.Header) -> int:
    for i, s in enumerate(header.signals):
        if s.description == LEAD:
            return i
    raise wfdb.WfdbError(f"record {header.record}: no {LEAD} lead in header")


def extract_beats(
    record: str,
    header: wfdb.Header,
    signal: np.ndarray[Any, Any],
    anns: list[wfdb.Annotation],
    table: Table,
    stats: dict[str, int],
) -> None:
    """Beat windows centred on annotated R peaks. RR intervals come from neighbouring labelled beats, so the
    first and last beat of a record (no pre/post RR) are dropped; windows crossing the record edge or containing
    invalid samples are dropped. All drops are counted in `stats`."""
    beats = [(a.sample, AAMI_CLASSES.index(AAMI_MAP[a.symbol])) for a in anns if a.symbol in AAMI_MAP]
    pre, post = round(WINDOW_PRE_S * header.fs), round(WINDOW_POST_S * header.fs)
    stats["dropped_no_rr"] += min(2, len(beats))
    for k in range(1, len(beats) - 1):
        s, cls = beats[k]
        if s - pre < 0 or s + post > len(signal):
            stats["dropped_edge"] += 1
            continue
        w = signal[s - pre : s + post]
        if np.isnan(w).any():
            stats["dropped_invalid"] += 1
            continue
        table.add(
            w,
            cls,
            subject_of(record),
            f"mitdb/{record}/{s}",
            rr_pre_s=(s - beats[k - 1][0]) / header.fs,
            rr_post_s=(beats[k + 1][0] - s) / header.fs,
        )


def extract_segments(
    record: str,
    header: wfdb.Header,
    signal: np.ndarray[Any, Any],
    anns: list[wfdb.Annotation],
    table: Table,
    stats: dict[str, int],
    seconds: float,
) -> None:
    """Non-overlapping segments from the record start. `r_peaks` holds every beat annotation (BEAT_SYMBOLS) inside the
    segment (segment-relative samples, -1 padded); y = 1 if any of them is S, V or F. The trailing partial segment
    and segments containing invalid samples are dropped; both are counted in `stats`."""
    size = round(seconds * header.fs)
    width = math.ceil(seconds * MAX_BEATS_PER_S)
    beats = [(a.sample, AAMI_MAP.get(a.symbol, "")) for a in anns if a.symbol in BEAT_SYMBOLS]
    count = len(signal) // size
    stats["dropped_tail_samples"] += len(signal) - count * size
    for k in range(count):
        start, end = k * size, (k + 1) * size
        w = signal[start:end]
        if np.isnan(w).any():
            stats["dropped_invalid"] += 1
            continue
        inside = [(s - start, cls) for s, cls in beats if start <= s < end]
        if len(inside) > width:
            raise wfdb.WfdbError(f"record {record}: {len(inside)} beats in {seconds:g} s exceed {MAX_BEATS_PER_S}/s")
        peaks = np.full(width, -1, dtype=np.int64)
        peaks[: len(inside)] = [s for s, _ in inside]
        y = int(any(cls in ECTOPIC for _, cls in inside))
        table.add(w, y, subject_of(record), f"mitdb/{record}/{start}-{end}", r_peaks=peaks, sampling_rate_hz=header.fs)


Extractor = Callable[[str, wfdb.Header, np.ndarray[Any, Any], list[wfdb.Annotation], Table, dict[str, int]], None]


def layout(settings: Mapping[str, Any]) -> tuple[Extractor, dict[str, int], dict[int, str], dict[str, Any]]:
    """Extractor, drop counters, label map and manifest description of the selected sample unit."""
    if settings.get("unit", "beat") == "beat":
        window = {"pre_s": WINDOW_PRE_S, "post_s": WINDOW_POST_S, "centre": "annotated beat (R peak)"}
        return (
            extract_beats,
            {"dropped_no_rr": 0, "dropped_edge": 0, "dropped_invalid": 0},
            dict(enumerate(AAMI_CLASSES)),
            {
                "unit": "beat",
                "window": window,
                "x_layout": "X[N, window_samples]; rr_pre_s / rr_post_s arrays in seconds",
            },
        )
    seconds = float(settings.get("segment_s", SEGMENT_S))
    return (
        partial(extract_segments, seconds=seconds),
        {"dropped_tail_samples": 0, "dropped_invalid": 0},
        {0: "no_SVF_beat", 1: "any_SVF_beat"},
        {
            "unit": "segment",
            "segment_s": seconds,
            "x_layout": "X[N, segment_samples] continuous MLII; r_peaks[N, max_beats] reference R peaks "
            "(segment-relative samples, -1 padded); sampling_rate_hz[N]",
            "r_peaks_source": "every MIT-BIH beat annotation (WFDB beat codes, incl. B, n, r, ? outside the AAMI map)",
        },
    )


class MitBihProvider:
    spec = DataSourceSpec(
        "physio_mitbih",
        "0.1.0",
        "dataset",
        "MIT-BIH Arrhythmia (PhysioNet mitdb 1.0.0): MLII beat windows, AAMI N/S/V/F/Q, inter-patient DS1/DS2",
        hosts=(HOST,),
        settings_schema=SETTINGS_SCHEMA,
    )

    def build(self, ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> DatasetDescriptor:
        up = Upstream(ctx.http, BASE)
        up.load_checksums()
        lic = up.license(settings)
        records = selected_records(settings, up.get_text("RECORDS").split())
        extract, stats, label_map, unit_extra = layout(settings)
        table = Table()
        fs_seen: set[float] = set()
        for i, rec in enumerate(records):
            check_cancel(ctx)
            header = wfdb.parse_header(up.get_text(f"{rec}.hea"))
            files = {s.file_name: up.get(s.file_name) for s in header.signals}
            phys = wfdb.to_physical(header, wfdb.read_digital(header, files))
            anns = wfdb.read_annotations(up.get(f"{rec}.atr"))
            fs_seen.add(header.fs)
            extract(rec, header, phys[:, lead_index(header)], anns, table, stats)
            ctx.progress("records", i + 1, len(records))
        if len(fs_seen) != 1:
            raise wfdb.WfdbError(f"records have different sampling rates {sorted(fs_seen)}")
        val = float(settings.get("val_fraction", 0.15))
        dev = seeded_partition(
            [subject_of(r) for r in records if r in DS1],
            {"search_val": val, "confirm_val": val},
            int(settings.get("seed", 0)),
        )
        assign = {**dev, "test": [subject_of(r) for r in records if r in DS2]}
        extra = {
            "dataset": "MIT-BIH Arrhythmia Database",
            "upstream_version": VERSION,
            "signal_type": "ECG",
            "sampling_rate_hz": fs_seen.pop(),
            "leads": [LEAD],
            "units": "mV",
            **unit_extra,
            "label_map": {str(k): v for k, v in label_map.items()},
            "aami_symbol_map": AAMI_MAP,
            "class_counts": class_counts(table.y, label_map),
            "records": records,
            "subject_of_record": {r: subject_of(r) for r in records},
            "excluded_records": EXCLUDED,
            f"dropped_{unit_extra['unit']}s": stats,
            "split": split_spec(assign, table.group),
            "split_source": "de Chazal et al. 2004 inter-patient DS1/DS2 (DS2 -> test, record 202 removed); "
            "DS1 -> seeded patient-level train/search_val/confirm_val",
        }
        # beat windows span WINDOW_PRE_S + WINDOW_POST_S = 0.70 s, so beats closer than that (HR > ~86 bpm) overlap
        overlapping = unit_extra["unit"] == "beat"
        return descriptor("physio_mitbih", VERSION, up, lic, table, ctx.out_dir, extra, overlapping_windows=overlapping)
