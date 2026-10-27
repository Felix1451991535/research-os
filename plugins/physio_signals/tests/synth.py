"""SYNTHETIC fixtures (CC0, generated here; no real patient data). Tiny WFDB/EDF encoders and fake PhysioNet
file trees served through researchos.sdk.testing.FakeHttp."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np
from researchos.contracts.http import HttpResponse
from researchos.sdk.testing import FakeHttp

ODC_BY = "SYNTHETIC test stand-in. Made available under the Open Data Commons Attribution License v1.0."
CC_BY = "SYNTHETIC test stand-in. Creative Commons Attribution 4.0 International Public License."

# ---------------------------------------------------------------- WFDB encoders


def checksum16(col: np.ndarray[Any, Any]) -> int:
    v = int(col.astype(np.int64).sum()) % 65536
    return v - 65536 if v >= 32768 else v


def enc212(flat: np.ndarray[Any, Any]) -> bytes:
    s = flat.astype(np.int64) & 0xFFF
    odd = len(s) % 2
    if odd:
        s = np.append(s, 0)
    a, b = s[0::2], s[1::2]
    raw = np.stack([a & 0xFF, ((a >> 8) & 0x0F) | (((b >> 8) & 0x0F) << 4), b & 0xFF], axis=1).astype(np.uint8)
    out = raw.tobytes()
    return out[:-1] if odd else out


def enc16(flat: np.ndarray[Any, Any]) -> bytes:
    return flat.astype("<i2").tobytes()


def wfdb_header(
    record: str,
    fs: float,
    digital: np.ndarray[Any, Any],
    file_name: str,
    fmt: int,
    names: list[str],
    gain: float,
    zero: int,
    style: str = "mit",
) -> str:
    lines = [f"{record} {digital.shape[1]} {fs:g} {digital.shape[0]}"]
    for i, name in enumerate(names):
        col = digital[:, i]
        g = f"{gain:g}" if style == "mit" else f"{gain:.1f}({zero})/mV"
        res = 12 if fmt == 212 else 16
        lines.append(f"{file_name} {fmt} {g} {res} {zero} {int(col[0])} {checksum16(col)} 0 {name}")
    return "\n".join(lines) + "\n# SYNTHETIC record\n"


def enc_atr(anns: list[tuple[int, int]], aux: Mapping[int, str] | None = None) -> bytes:
    out, t = bytearray(), 0
    for i, (sample, code) in enumerate(anns):
        d = sample - t
        if d > 1023:
            out += (59 << 10).to_bytes(2, "little") + ((d >> 16) & 0xFFFF).to_bytes(2, "little")
            out += (d & 0xFFFF).to_bytes(2, "little")
            d = 0
        out += ((code << 10) | d).to_bytes(2, "little")
        t = sample
        text = (aux or {}).get(i)
        if text:
            payload = text.encode("latin-1")
            out += ((63 << 10) | len(payload)).to_bytes(2, "little") + payload + (b"\0" if len(payload) % 2 else b"")
    return bytes(out + b"\0\0")


# MIT codes: N=1, V=5, A=8, F=6, Q=13, '+'=28 (rhythm, non-beat)
BEAT_CYCLE = (1, 1, 5, 1, 8, 1, 6, 1, 13, 1)


def mit_record(record: str, seed: int, seconds: float = 12.0, mlii_first: bool = True) -> dict[str, bytes]:
    fs, n = 360.0, int(seconds * 360)
    rng = np.random.default_rng(seed)
    beats = list(range(150, n - 150, 288))
    sig = rng.normal(0, 4, size=(n, 2))
    for b in beats:
        sig[b - 3 : b + 4, :] += 180
    digital = (sig + 1024).round().astype(np.int64)
    names = ["MLII", "V1"] if mlii_first else ["V1", "MLII"]
    anns = [(0, 28)] + [(b, BEAT_CYCLE[k % len(BEAT_CYCLE)]) for k, b in enumerate(beats)]
    return {
        f"{record}.hea": wfdb_header(record, fs, digital, f"{record}.dat", 212, names, 200, 1024).encode(),
        f"{record}.dat": enc212(digital.reshape(-1)),
        f"{record}.atr": enc_atr(anns, {0: "(N"}),
    }


# ---------------------------------------------------------------- EDF writer


def _pad(text: str, width: int) -> bytes:
    return text.encode("ascii")[:width].ljust(width, b" ")


def _tal_block(r: int, record_s: int, notes: list[tuple[float, float, str]], width: int) -> bytes:
    """EDF+ annotation bytes of record r: time-keeping TAL, then the notes whose onset falls in this record."""
    block = f"+{r * record_s}\x14\x14\x00".encode()
    for onset, dur, text in notes:
        if r * record_s <= onset < (r + 1) * record_s:
            block += f"+{onset:g}\x15{dur:g}\x14{text}\x14\x00".encode()
    assert len(block) <= width, "annotation channel too small for this record"
    return block.ljust(width, b"\x00")


def edf_bytes(
    labels: list[str],
    fs: int,
    signals: np.ndarray[Any, Any],
    record_s: int = 1,
    annotations: list[tuple[float, float, str]] | None = None,
) -> bytes:
    """EDF (or EDF+ with an "EDF Annotations" channel of 60 samples per record when `annotations` is given)."""
    if annotations is not None:
        labels = [*labels, "EDF Annotations"]
    ns, n = signals.shape[0] + (annotations is not None), signals.shape[1]
    spr = fs * record_s
    n_rec = n // spr
    head = (
        _pad("0", 8)
        + _pad("SYNTHETIC", 80)
        + _pad("SYNTHETIC", 80)
        + _pad("01.01.01", 8)
        + _pad("00.00.00", 8)
        + _pad(str(256 * (ns + 1)), 8)
        + _pad("", 44)
        + _pad(str(n_rec), 8)
        + _pad(str(record_s), 8)
        + _pad(str(ns), 4)
    )
    cols = [
        (labels, 16),
        ([""] * ns, 80),
        (["uV"] * ns, 8),
        (["-3200"] * ns, 8),
        (["3200"] * ns, 8),
        (["-32768"] * ns, 8),
        (["32767"] * ns, 8),
        ([""] * ns, 80),
        ([str(spr)] * (ns - (annotations is not None)) + ["60"] * (annotations is not None), 8),
        ([""] * ns, 32),
    ]
    for values, width in cols:
        head += b"".join(_pad(v, width) for v in values)
    dig = np.clip(np.round((signals + 3200) * 65535 / 6400 - 32768), -32768, 32767).astype("<i2")
    notes = annotations or []
    body = b"".join(
        dig[:, r * spr : (r + 1) * spr].tobytes()
        + (_tal_block(r, record_s, notes, 120) if annotations is not None else b"")
        for r in range(n_rec)
    )
    return head + body


# ---------------------------------------------------------------- fake PhysioNet trees


def sha256sums(files: Mapping[str, bytes], tamper: str | None = None) -> bytes:
    lines = []
    for path, data in sorted(files.items()):
        h = hashlib.sha256(data).hexdigest()
        lines.append(f"{'0' * 64 if path == tamper else h} {path}")
    return ("\n".join(lines) + "\n").encode()


def tree_http(
    base: str,
    files: Mapping[str, bytes],
    *,
    sums: bool = True,
    tamper: str | None = None,
    license_text: str | None = ODC_BY,
) -> FakeHttp:
    routes: dict[str, HttpResponse | Callable[[str], HttpResponse]] = {
        base + p: HttpResponse(200, {}, d, base + p) for p, d in files.items()
    }
    data_only = dict(files)
    routes[base + "SHA256SUMS.txt"] = (
        HttpResponse(200, {}, sha256sums(data_only, tamper), base + "SHA256SUMS.txt")
        if sums
        else HttpResponse(404, {}, b"not found", base + "SHA256SUMS.txt")
    )
    lic = license_text.encode() if license_text is not None else b""
    routes[base + "LICENSE.txt"] = HttpResponse(200 if license_text is not None else 404, {}, lic, base + "LICENSE.txt")
    return FakeHttp(routes, ("physionet.org",))


MITDB = "https://physionet.org/files/mitdb/1.0.0/"
MIT_RECORDS = ("101", "106", "108", "109", "112", "100", "103", "105")


def mitdb_files(records: tuple[str, ...] = MIT_RECORDS) -> dict[str, bytes]:
    files: dict[str, bytes] = {"RECORDS": ("\n".join(records) + "\n").encode()}
    for i, r in enumerate(records):
        files.update(mit_record(r, seed=i, mlii_first=(r != "109")))
    return files


PTBXL = "https://physionet.org/files/ptb-xl/1.0.3/"
SCP = (
    ",description,diagnostic,form,rhythm,diagnostic_class,diagnostic_subclass\n"
    "NORM,normal ECG,1.0,,,NORM,NORM\nIMI,inferior MI,1.0,,,MI,IMI\nLVH,left ventricular hypertrophy,1.0,,,HYP,LVH\n"
    "NDT,non-diagnostic T abnormalities,1.0,,,STTC,STTC\nSR,sinus rhythm,,,1.0,,\n"
)
LEADS = ["I", "II", "III", "AVR", "AVL", "AVF", "V1", "V2", "V3", "V4", "V5", "V6"]
# (ecg_id, patient_id, strat_fold, scp_codes)
PTB_ROWS = (
    (1, 101, 1, "{'NORM': 100.0, 'SR': 0.0}"),
    (2, 101, 1, "{'NORM': 80.0}"),
    (3, 102, 2, "{'IMI': 50.0}"),
    (4, 103, 3, "{'LVH': 100.0}"),
    (5, 104, 4, "{'NDT': 100.0}"),
    (6, 105, 5, "{'NORM': 100.0}"),
    (7, 106, 6, "{'IMI': 100.0}"),
    (8, 107, 7, "{'LVH': 100.0}"),
    (9, 108, 8, "{'NORM': 100.0}"),
    (10, 109, 9, "{'NORM': 100.0}"),
    (11, 110, 9, "{'IMI': 100.0}"),
    (12, 111, 10, "{'NORM': 100.0}"),
    (13, 112, 10, "{'IMI': 100.0}"),
    (14, 113, 3, "{'IMI': 100.0, 'LVH': 50.0}"),
    (15, 114, 4, "{'SR': 0.0}"),
)


def ptb_record(ecg_id: int, seed: int) -> dict[str, bytes]:
    name = f"{ecg_id:05d}_lr"
    rng = np.random.default_rng(seed)
    digital = rng.integers(-300, 300, size=(100, 12))
    head = wfdb_header(name, 100, digital, f"{name}.dat", 16, LEADS, 1000.0, 0, style="ptb")
    folder = "records100/00000"
    return {f"{folder}/{name}.hea": head.encode(), f"{folder}/{name}.dat": enc16(digital.reshape(-1))}


def ptbxl_files(rows: tuple[tuple[int, int, int, str], ...] = PTB_ROWS) -> dict[str, bytes]:
    lines = ["ecg_id,patient_id,age,sex,scp_codes,strat_fold,filename_lr,filename_hr"]
    files: dict[str, bytes] = {"scp_statements.csv": SCP.encode()}
    for ecg, pat, fold, codes in rows:
        lines.append(f'{ecg},{pat}.0,,0,"{codes}",{fold},records100/00000/{ecg:05d}_lr,records500/00000/{ecg:05d}_hr')
        files.update(ptb_record(ecg, ecg))
    files["ptbxl_database.csv"] = ("\n".join(lines) + "\n").encode()
    return files


CHBMIT = "https://physionet.org/files/chbmit/1.0.0/"
EEG_LABELS = ["FP1-F7", "F7-T7", "T8-P8-0", "FZ-CZ", "T8-P8-1"]
EEG_CHANNELS = ["FP1-F7", "F7-T7", "T8-P8"]


def chb_summary(case: str, files: list[tuple[str, list[tuple[int, int]]]]) -> str:
    out = ["Data Sampling Rate: 32 Hz", "*" * 25, ""]
    for name, seizures in files:
        out += [
            f"File Name: {name}",
            "File Start Time: 00:00:00",
            "File End Time: 00:01:04",
            f"Number of Seizures in File: {len(seizures)}",
        ]
        for k, (s, e) in enumerate(seizures, 1):
            out += [f"Seizure {k} Start Time: {s} seconds", f"Seizure {k} End Time: {e} seconds"]
        out.append("")
    return "\n".join(out)


def chbmit_files(cases: tuple[str, ...] = ("chb01", "chb02", "chb03", "chb04", "chb21")) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    records = []
    for i, case in enumerate(cases):
        rng = np.random.default_rng(i)
        entries = [(f"{case}_01.edf", [(20, 28)]), (f"{case}_02.edf", [])]
        for name, _ in entries:
            files[f"{case}/{name}"] = edf_bytes(EEG_LABELS, 32, rng.normal(0, 50, size=(len(EEG_LABELS), 64 * 32)))
            records.append(f"{case}/{name}")
        files[f"{case}/{case}-summary.txt"] = chb_summary(case, entries).encode()
    files["RECORDS"] = ("\n".join(records) + "\n").encode()
    return files


# ---------------------------------------------------------------- eegmmidb (EEG Motor Movement/Imagery)

EEGMMI = "https://physionet.org/files/eegmmidb/1.0.0/"
MMI_LABELS = ["Fc5.", "C3..", "Cz..", "C4..", "Fp1."]


def mmi_run(fs: int, seed: int, seconds: int = 34) -> bytes:
    """Alternating T0 / T1 / T0 / T2 trials of 4.2 s; a 10 Hz rhythm is weaker over the hemisphere contralateral
    to the imagined fist (C4 for T1 = left, C3 for T2 = right). SYNTHETIC."""
    rng = np.random.default_rng(seed)
    t = np.arange(seconds * fs) / fs
    sig = rng.normal(0, 5, (len(MMI_LABELS), t.size))
    notes = []
    for k, onset in enumerate(np.arange(0, seconds - 4.2, 4.2)):
        code = ("T0", "T1", "T0", "T2")[k % 4]
        notes.append((float(onset), 4.1, code))
        seg = (t >= onset) & (t < onset + 4.1)
        alpha = 20 * np.sin(2 * np.pi * 10 * t[seg])
        sig[1, seg] += alpha * (0.3 if code == "T2" else 1.0)  # C3
        sig[3, seg] += alpha * (0.3 if code == "T1" else 1.0)  # C4
    return edf_bytes(MMI_LABELS, fs, sig, annotations=notes)


def eegmmi_files(
    subjects: tuple[str, ...] = ("S001", "S002", "S003", "S004", "S005", "S006"), slow: tuple[str, ...] = ("S006",)
) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for i, s in enumerate(subjects):
        for r in (4, 8, 12):
            files[f"{s}/{s}R{r:02d}.edf"] = mmi_run(128 if s in slow else 160, seed=i * 100 + r)
    return files
