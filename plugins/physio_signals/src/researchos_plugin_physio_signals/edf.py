"""Minimal EDF reader (European Data Format, Kemp et al. 1992) for CHB-MIT scalp EEG.

Strict: header fields are validated, the data section must match the declared size exactly, and the reserved
"EDF Annotations" channel (EDF+) is excluded from signals. Raises EdfError on malformed or truncated input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


class EdfError(ValueError):
    """Malformed or truncated EDF input."""


@dataclass(frozen=True)
class EdfHeader:
    n_records: int
    record_duration_s: float
    labels: tuple[str, ...]
    samples_per_record: tuple[int, ...]
    phys_min: tuple[float, ...]
    phys_max: tuple[float, ...]
    dig_min: tuple[int, ...]
    dig_max: tuple[int, ...]
    phys_dim: tuple[str, ...]
    header_bytes: int


def _field(raw: bytes, start: int, width: int) -> str:
    return raw[start : start + width].decode("ascii", errors="replace").strip()


def _num(text: str, what: str) -> float:
    try:
        return float(text)
    except ValueError as e:
        raise EdfError(f"EDF header: {what} is not numeric: {text!r}") from e


def _column(raw: bytes, offset: int, width: int, ns: int) -> list[str]:
    return [_field(raw, offset + k * width, width) for k in range(ns)]


_SIGNAL_FIELDS = (
    ("label", 16),
    ("transducer", 80),
    ("dim", 8),
    ("pmin", 8),
    ("pmax", 8),
    ("dmin", 8),
    ("dmax", 8),
    ("prefilter", 80),
    ("spr", 8),
    ("reserved", 32),
)


def parse_header(raw: bytes) -> EdfHeader:
    if len(raw) < 256:
        raise EdfError("EDF header truncated (< 256 bytes)")
    ns = int(_num(_field(raw, 252, 4), "signal count"))
    header_bytes = int(_num(_field(raw, 184, 8), "header size"))
    if ns <= 0 or header_bytes != 256 * (ns + 1) or len(raw) < header_bytes:
        raise EdfError("EDF header inconsistent or truncated (signal count / header size)")
    cols: dict[str, list[str]] = {}
    offset = 256
    for name, width in _SIGNAL_FIELDS:
        cols[name] = _column(raw, offset, width, ns)
        offset += width * ns
    hdr = EdfHeader(
        n_records=int(_num(_field(raw, 236, 8), "data record count")),
        record_duration_s=_num(_field(raw, 244, 8), "data record duration"),
        labels=tuple(cols["label"]),
        samples_per_record=tuple(int(_num(v, "samples per record")) for v in cols["spr"]),
        phys_min=tuple(_num(v, "physical minimum") for v in cols["pmin"]),
        phys_max=tuple(_num(v, "physical maximum") for v in cols["pmax"]),
        dig_min=tuple(int(_num(v, "digital minimum")) for v in cols["dmin"]),
        dig_max=tuple(int(_num(v, "digital maximum")) for v in cols["dmax"]),
        phys_dim=tuple(cols["dim"]),
        header_bytes=header_bytes,
    )
    if hdr.record_duration_s <= 0 or any(d1 <= d0 for d0, d1 in zip(hdr.dig_min, hdr.dig_max, strict=True)):
        raise EdfError("EDF header: invalid record duration or digital range")
    return hdr


def signal_indices(hdr: EdfHeader) -> list[int]:
    return [i for i, lab in enumerate(hdr.labels) if lab != "EDF Annotations"]


def read_edf(raw: bytes) -> tuple[EdfHeader, np.ndarray[Any, np.dtype[np.float32]]]:
    """Returns (header, physical signals [n_signals, n_samples]) for the non-annotation channels."""
    hdr = parse_header(raw)
    idx = signal_indices(hdr)
    if not idx or len({hdr.samples_per_record[i] for i in idx}) != 1:
        raise EdfError("EDF: signals must share one sampling rate")
    rec_len = sum(hdr.samples_per_record)
    payload = len(raw) - hdr.header_bytes
    if hdr.n_records < 0 or payload != hdr.n_records * rec_len * 2:
        raise EdfError(f"EDF data section is {payload} bytes; header declares {hdr.n_records} x {rec_len * 2}")
    data = np.frombuffer(raw, dtype="<i2", offset=hdr.header_bytes).reshape(hdr.n_records, rec_len)
    starts = np.cumsum((0, *hdr.samples_per_record))
    out = np.empty((len(idx), hdr.n_records * hdr.samples_per_record[idx[0]]), dtype=np.float32)
    for row, i in enumerate(idx):
        dig = data[:, starts[i] : starts[i + 1]].reshape(-1).astype(np.float64)
        scale = (hdr.phys_max[i] - hdr.phys_min[i]) / (hdr.dig_max[i] - hdr.dig_min[i])
        out[row] = ((dig - hdr.dig_min[i]) * scale + hdr.phys_min[i]).astype(np.float32)
    return hdr, out


def sampling_rate(hdr: EdfHeader) -> float:
    return hdr.samples_per_record[signal_indices(hdr)[0]] / hdr.record_duration_s


ANNOTATIONS = "EDF Annotations"


def read_annotations(raw: bytes) -> list[tuple[float, float | None, str]]:
    """EDF+ time-stamped annotation lists (TALs): (onset s, duration s or None, text) for every non-empty text.
    Each record's leading time-keeping TAL (empty text) is skipped. Raises EdfError on malformed TALs."""
    hdr = parse_header(raw)
    ann = [i for i, lab in enumerate(hdr.labels) if lab == ANNOTATIONS]
    if not ann:
        return []
    rec_len = sum(hdr.samples_per_record) * 2
    start = sum(hdr.samples_per_record[: ann[0]]) * 2
    width = hdr.samples_per_record[ann[0]] * 2
    out: list[tuple[float, float | None, str]] = []
    for r in range(hdr.n_records):
        offset = hdr.header_bytes + r * rec_len + start
        for tal in raw[offset : offset + width].split(b"\x14\x00"):
            if not tal.strip(b"\x00"):
                continue
            stamp, *texts = tal.strip(b"\x00").split(b"\x14")
            onset_raw, _, dur_raw = stamp.partition(b"\x15")
            try:
                onset, duration = float(onset_raw), float(dur_raw) if dur_raw else None
            except ValueError as e:
                raise EdfError("EDF+: malformed annotation time stamp") from e
            out += [(onset, duration, t.decode("utf-8", "replace")) for t in texts if t]
    return out
