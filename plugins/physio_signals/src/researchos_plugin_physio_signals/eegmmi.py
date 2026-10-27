"""physio_eegmmi: PhysioNet EEG Motor Movement/Imagery Dataset (eegmmidb 1.0.0) as event-locked EEG windows for
left- vs right-fist trials (ADR-179). Candidate dataset for the V1 end-to-end EEG acceptance (owner decision pending);
nothing is downloaded unless the owner builds it.

Trials: runs 4, 8, 12 (imagined) or 3, 7, 11 (executed) - annotation T1 = left fist, T2 = right fist, T0 = rest
(not used). A window starts at each T1/T2 onset and lasts `window_s`. Subjects are the groups; a seeded subject-level
partition gives train / search_val / confirm_val / test (no official split exists). Records whose sampling rate
differs from the others, that lack a requested channel, or whose annotations are malformed are skipped and listed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from researchos.contracts.datasources import DatasetBuildContext, DatasetDescriptor, DataSourceSpec

from . import edf
from .common import Table, check_cancel, class_counts, descriptor, seeded_partition, split_spec
from .fetch import HOST, LocalCopy, Upstream, UpstreamError

BASE = "https://physionet.org/files/eegmmidb/1.0.0/"
VERSION = "1.0.0"
RUNS = {"imagery_left_right": (4, 8, 12), "movement_left_right": (3, 7, 11)}
LABELS = {"T1": 0, "T2": 1}
DEFAULT_CHANNELS = ("C3", "CZ", "C4")
SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["subjects"],
    "properties": {
        "subjects": {
            "type": "array",
            "items": {"type": "string", "pattern": "^S[0-9]{3}$"},
            "minItems": 3,
            "uniqueItems": True,
            "description": "explicit subjects (e.g. S001..S020): downloads are deliberate; >= 3 so train, validation "
            "and test each get a subject",
        },
        "task": {"enum": list(RUNS), "default": "imagery_left_right"},
        "channels": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
        "window_s": {"type": "number", "minimum": 0.5, "maximum": 4.0, "default": 4.0},
        "seed": {"type": "integer", "minimum": 0},
        "val_fraction": {"type": "number", "minimum": 0, "maximum": 0.4},
        "test_fraction": {"type": "number", "minimum": 0, "maximum": 0.5},
        "license_override": {"type": "string", "minLength": 1},
        "local_dir": {
            "type": "string",
            "minLength": 1,
            "description": "build from an owner's local copy of "
            "eegmmidb 1.0.0 (inside a file root; layout S001/S001R04.edf) instead of downloading",
        },
    },
    "additionalProperties": False,
}


def channel_rows(labels: tuple[str, ...], wanted: tuple[str, ...]) -> list[int] | None:
    """eegmmidb labels carry padding dots ("C3..", "Cz.."); compare without them, case-insensitively."""
    norm = [lab.strip().strip(".").upper() for lab in labels]
    rows = [next((i for i, lab in enumerate(norm) if lab == ch.upper()), -1) for ch in wanted]
    return None if -1 in rows else rows


def add_record(
    name: str, subject: str, raw: bytes, settings: Mapping[str, Any], table: Table, skipped: dict[str, str]
) -> float | None:
    """Event-locked windows of one run; returns its sampling rate, or None when the record is skipped."""
    try:
        hdr, sig = edf.read_edf(raw)
        events = edf.read_annotations(raw)
    except edf.EdfError as e:
        skipped[name] = f"unreadable EDF+: {e}"
        return None
    fs = edf.sampling_rate(hdr)
    idx = edf.signal_indices(hdr)
    rows = channel_rows(tuple(hdr.labels[i] for i in idx), tuple(settings.get("channels") or DEFAULT_CHANNELS))
    if rows is None:
        skipped[name] = "missing one of the requested channels"
        return None
    win = int(round(float(settings.get("window_s", 4.0)) * fs))
    for k, (onset, _, text) in enumerate(events):
        start = int(round(onset * fs))
        if text in LABELS and start + win <= sig.shape[1]:
            table.add(sig[rows, start : start + win], LABELS[text], subject, f"eegmmi/{name}/{k}", sampling_rate_hz=fs)
    return fs


def source(ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> Upstream:
    """PhysioNet download, or the owner's local copy when `local_dir` is set (ADR-189)."""
    if not settings.get("local_dir"):
        return Upstream(ctx.http, BASE)
    if ctx.local is None:
        raise UpstreamError("local_dir needs read access to an owner file root (capability files:read)")
    return LocalCopy(ctx.http, str(settings["local_dir"]).rstrip("/") + "/", local=ctx.local, upstream=BASE)


class EegMmiProvider:
    spec = DataSourceSpec(
        "physio_eegmmi",
        "0.1.0",
        "dataset",
        "PhysioNet EEG Motor Movement/Imagery (eegmmidb 1.0.0): event-locked left/right fist windows, subject groups",
        hosts=(HOST,),
        settings_schema=SETTINGS_SCHEMA,
    )

    def build(self, ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> DatasetDescriptor:
        up = source(ctx, settings)
        up.load_checksums()
        lic = up.license(settings)
        task = str(settings.get("task", "imagery_left_right"))
        names = [(s, f"{s}/{s}R{r:02d}.edf") for s in settings["subjects"] for r in RUNS[task]]
        table, rates, skipped = Table(), dict[str, float](), dict[str, str]()
        for i, (subject, name) in enumerate(names):
            check_cancel(ctx)
            fs = add_record(name, subject, up.get(name), settings, table, skipped)
            if fs is not None:
                rates[name] = fs
            ctx.progress("records", i + 1, len(names))
        common = max(set(rates.values()), key=list(rates.values()).count) if rates else None
        for name, fs in rates.items():  # keep one sampling rate; e.g. a 128 Hz record among 160 Hz ones is listed
            if fs != common:
                skipped[name] = f"sampling rate {fs:g} Hz differs from {common:g} Hz"
        if any(fs != common for fs in rates.values()):
            table = _without(table, {n for n, fs in rates.items() if fs != common})
        val, test = float(settings.get("val_fraction", 0.15)), float(settings.get("test_fraction", 0.2))
        assign = seeded_partition(
            table.group, {"test": test, "search_val": val, "confirm_val": val}, int(settings.get("seed", 0))
        )
        label_map = {0: "left_fist", 1: "right_fist"}
        extra = {
            "dataset": "EEG Motor Movement/Imagery Dataset",
            "upstream_version": VERSION,
            "signal_type": "EEG",
            "task": task,
            "runs": list(RUNS[task]),
            "sampling_rate_hz": common,
            "channels": list(settings.get("channels") or DEFAULT_CHANNELS),
            "units": "uV (EDF physical dimension)",
            "window_s": float(settings.get("window_s", 4.0)),
            "window_anchor": "T1/T2 annotation onset",
            "x_layout": "X[N, channels, window_samples]; sampling_rate_hz[N]",
            "label_map": {str(k): v for k, v in label_map.items()},
            "class_counts": class_counts(table.y, label_map),
            "records": [n for _, n in names],
            "skipped_records": skipped,
            "split": split_spec(assign, table.group),
            "split_source": "no official split; seeded subject-level partition",
        }
        return descriptor("physio_eegmmi", VERSION, up, lic, table, ctx.out_dir, extra)


def _without(table: Table, records: set[str]) -> Table:
    out = Table()
    for j, sid in enumerate(table.sample_id):
        if sid.split("/")[1] + "/" + sid.split("/")[2] not in records:
            out.add(table.X[j], table.y[j], table.group[j], sid, **{k: v[j] for k, v in table.extra.items()})
    return out
