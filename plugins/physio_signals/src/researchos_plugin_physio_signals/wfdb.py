"""Minimal WFDB reader: header (.hea), signal files in formats 16 and 212, MIT-format annotation files.

Strict by design: malformed, truncated or checksum-failing input raises WfdbError; samples are never padded,
guessed or silently dropped. Multi-segment records and per-signal skew/offset/multi-frame layouts are refused.
Reference: WFDB file formats (header(5), signal(5), annot(5)), https://physionet.org/physiotools/wag/.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np


class WfdbError(ValueError):
    """Malformed, truncated or inconsistent WFDB input."""


@dataclass(frozen=True)
class SignalSpec:
    file_name: str
    fmt: int
    gain: float  # ADC units per physical unit
    baseline: int
    units: str | None  # None when the header does not state it
    adc_res: int | None
    adc_zero: int
    init_value: int | None
    checksum: int | None
    description: str | None


@dataclass(frozen=True)
class Header:
    record: str
    n_signals: int
    fs: float
    n_samples: int | None
    signals: tuple[SignalSpec, ...]
    comments: tuple[str, ...]


@dataclass(frozen=True)
class Annotation:
    sample: int
    code: int
    symbol: str | None
    aux: str | None = None


SUPPORTED_FORMATS = (16, 212)
_GAIN = re.compile(r"^(?P<gain>[-+0-9.eE]+)(?:\((?P<base>-?\d+)\))?(?:/(?P<units>\S+))?$")
_FS = re.compile(r"^(?P<fs>[0-9.eE+]+)(?:/[0-9.eE+]+)?(?:\(-?[0-9.]+\))?$")


def _int(tok: str, what: str) -> int:
    try:
        return int(tok)
    except ValueError as e:
        raise WfdbError(f"header: {what} is not an integer: {tok!r}") from e


def _opt_int(toks: list[str], i: int, what: str) -> int | None:
    return _int(toks[i], what) if len(toks) > i else None


def _record_line(line: str) -> tuple[str, int, float, int | None]:
    toks = line.split()
    if len(toks) < 3:
        raise WfdbError("header: record line needs name, signal count and sampling frequency")
    if "/" in toks[0]:
        raise WfdbError("header: multi-segment records are not supported")
    m = _FS.match(toks[2])
    if not m or float(m["fs"]) <= 0:
        raise WfdbError(f"header: invalid sampling frequency {toks[2]!r}")
    return toks[0], _int(toks[1], "signal count"), float(m["fs"]), _opt_int(toks, 3, "sample count")


def _signal_line(line: str) -> SignalSpec:
    toks = line.split()
    if len(toks) < 3:
        raise WfdbError(f"header: signal line too short: {line!r}")
    if not toks[1].isdigit():
        raise WfdbError(f"header: unsupported format spec {toks[1]!r} (skew/offset/multi-frame not supported)")
    fmt = int(toks[1])
    if fmt not in SUPPORTED_FORMATS:
        raise WfdbError(f"header: signal format {fmt} not supported (only {SUPPORTED_FORMATS})")
    g = _GAIN.match(toks[2])
    if not g:
        raise WfdbError(f"header: invalid gain field {toks[2]!r}")
    gain = float(g["gain"])
    if gain <= 0:
        raise WfdbError("header: uncalibrated signal (gain <= 0) cannot be converted to physical units")
    adc_zero = _opt_int(toks, 4, "ADC zero") or 0
    baseline = int(g["base"]) if g["base"] is not None else adc_zero
    return SignalSpec(
        file_name=toks[0],
        fmt=fmt,
        gain=gain,
        baseline=baseline,
        units=g["units"],
        adc_res=_opt_int(toks, 3, "ADC resolution"),
        adc_zero=adc_zero,
        init_value=_opt_int(toks, 5, "initial value"),
        checksum=_opt_int(toks, 6, "checksum"),
        description=" ".join(toks[8:]) or None,
    )


def parse_header(text: str) -> Header:
    lines = [ln.strip() for ln in text.splitlines()]
    comments = tuple(ln[1:].strip() for ln in lines if ln.startswith("#"))
    body = [ln for ln in lines if ln and not ln.startswith("#")]
    if not body:
        raise WfdbError("header: empty")
    record, nsig, fs, nsamp = _record_line(body[0])
    signals = tuple(_signal_line(ln) for ln in body[1 : 1 + nsig])
    if len(signals) != nsig:
        raise WfdbError(f"header: declares {nsig} signals but describes {len(signals)}")
    return Header(record, nsig, fs, nsamp, signals, comments)


def _decode_212(data: bytes, total: int) -> np.ndarray[Any, np.dtype[np.int32]]:
    n_pairs = (total + 1) // 2
    need = n_pairs * 3 - (1 if total % 2 else 0)
    if len(data) < need:
        raise WfdbError(f"signal file truncated: {len(data)} bytes, need {need}")
    if len(data) > n_pairs * 3:
        raise WfdbError(f"signal file longer than the header declares ({len(data)} > {n_pairs * 3} bytes)")
    raw = np.frombuffer(data[: n_pairs * 3].ljust(n_pairs * 3, b"\0"), dtype=np.uint8).reshape(-1, 3).astype(np.int32)
    s0 = raw[:, 0] | ((raw[:, 1] & 0x0F) << 8)
    s1 = raw[:, 2] | ((raw[:, 1] & 0xF0) << 4)
    out = np.empty(n_pairs * 2, dtype=np.int32)
    out[0::2], out[1::2] = s0, s1
    out = out[:total]
    out[out > 2047] -= 4096
    return out


def _decode_16(data: bytes, total: int) -> np.ndarray[Any, np.dtype[np.int32]]:
    if len(data) != 2 * total:
        kind = "truncated" if len(data) < 2 * total else "longer than the header declares"
        raise WfdbError(f"signal file {kind}: {len(data)} bytes, expected {2 * total}")
    return np.frombuffer(data, dtype="<i2").astype(np.int32)


def _file_groups(header: Header) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for i, s in enumerate(header.signals):
        groups.setdefault(s.file_name, []).append(i)
    for name, idx in groups.items():
        if len({header.signals[i].fmt for i in idx}) != 1:
            raise WfdbError(f"signal file {name}: mixed formats are not supported")
    return groups


def _check_signal(spec: SignalSpec, col: np.ndarray[Any, np.dtype[np.int32]]) -> None:
    if spec.init_value is not None and len(col) and int(col[0]) != spec.init_value:
        raise WfdbError(f"{spec.file_name}: first sample {int(col[0])} != header initial value {spec.init_value}")
    if spec.checksum is not None and (int(col.astype(np.int64).sum()) - spec.checksum) % 65536 != 0:
        raise WfdbError(f"{spec.file_name}: signal checksum mismatch (corrupt or truncated data)")


def read_digital(header: Header, files: Mapping[str, bytes]) -> np.ndarray[Any, np.dtype[np.int32]]:
    """Digital samples, shape [n_samples, n_signals], verified against header initial values and checksums."""
    if header.n_samples is None:
        raise WfdbError("header: sample count missing; refusing to infer record length")
    out = np.empty((header.n_samples, header.n_signals), dtype=np.int32)
    for name, idx in _file_groups(header).items():
        if name not in files:
            raise WfdbError(f"signal file {name} not provided")
        total = header.n_samples * len(idx)
        fmt = header.signals[idx[0]].fmt
        flat = _decode_212(files[name], total) if fmt == 212 else _decode_16(files[name], total)
        frame = flat.reshape(header.n_samples, len(idx))
        for j, i in enumerate(idx):
            _check_signal(header.signals[i], frame[:, j])
            out[:, i] = frame[:, j]
    return out


def to_physical(header: Header, digital: np.ndarray[Any, np.dtype[np.int32]]) -> np.ndarray[Any, np.dtype[np.float32]]:
    """Physical units; WFDB invalid-sample codes (-2048 in 212, -32768 in 16) become NaN."""
    phys = np.empty(digital.shape, dtype=np.float32)
    for i, s in enumerate(header.signals):
        col = digital[:, i]
        invalid = col == (-2048 if s.fmt == 212 else -32768)
        vals = ((col - s.baseline) / s.gain).astype(np.float32)
        vals[invalid] = np.nan
        phys[:, i] = vals
    return phys


# MIT annotation codes (WFDB ecgcodes.h) for the labels this plugin uses; others keep symbol None.
ANNOTATION_SYMBOLS: dict[int, str] = {
    1: "N",
    2: "L",
    3: "R",
    4: "a",
    5: "V",
    6: "F",
    7: "J",
    8: "A",
    9: "S",
    10: "E",
    11: "j",
    12: "/",
    13: "Q",
    14: "~",
    16: "|",
    18: "s",
    19: "T",
    22: '"',
    23: "=",
    24: "p",
    25: "B",
    26: "^",
    27: "t",
    28: "+",
    29: "u",
    30: "?",
    31: "!",
    32: "[",
    33: "]",
    34: "e",
    35: "n",
    36: "@",
    37: "x",
    38: "f",
    39: "(",
    40: ")",
    41: "r",
}
_SKIP, _NUM, _SUB, _CHN, _AUX = 59, 60, 61, 62, 63


def _word(data: bytes, i: int) -> int:
    return data[i] | (data[i + 1] << 8)


def _skip_interval(data: bytes, i: int) -> int:
    if i + 4 > len(data):
        raise WfdbError("annotation file truncated inside a SKIP record")
    value = (_word(data, i) << 16) | _word(data, i + 2)
    return value - (1 << 32) if value & 0x80000000 else value


def _with_aux(anns: list[Annotation], payload: bytes) -> None:
    if not anns:
        raise WfdbError("annotation file: AUX record before any annotation")
    last = anns[-1]
    anns[-1] = Annotation(last.sample, last.code, last.symbol, payload.decode("latin-1").rstrip("\0"))


def read_annotations(data: bytes) -> list[Annotation]:
    """Parse an MIT-format annotation file (.atr). Requires the 0x0000 end marker (truncation -> WfdbError)."""
    if len(data) % 2:
        raise WfdbError("annotation file has odd length (truncated)")
    anns: list[Annotation] = []
    t, i = 0, 0
    while i + 2 <= len(data):
        word = _word(data, i)
        i += 2
        code, val = word >> 10, word & 0x3FF
        if word == 0:
            return anns
        if code == _SKIP:
            t += _skip_interval(data, i)
            i += 4
        elif code == _AUX:
            if i + val > len(data):
                raise WfdbError("annotation file truncated inside an AUX record")
            _with_aux(anns, data[i : i + val])
            i += val + (val & 1)
        elif code not in (_NUM, _SUB, _CHN):
            t += val
            anns.append(Annotation(t, code, ANNOTATION_SYMBOLS.get(code)))
    raise WfdbError("annotation file truncated (no end-of-file marker)")
