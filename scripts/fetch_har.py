"""Download UCI HAR (dataset 240, CC BY 4.0 per the UCI page — verify before use) and convert it into two
Research OS datasets (features: X (n,561); raw: X (n,128,9) body-acc/gyro/total-acc windows, 50% overlap).
Records the observed sha256 of the download in the manifest (no hash is invented: first download = reference).

Usage: python scripts/fetch_har.py <out_dir> [--zip existing.zip]
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import httpx
import numpy as np

URL = "https://archive.ics.uci.edu/static/public/240/human+activity+recognition+using+smartphones.zip"
SIGNALS = ["body_acc_x", "body_acc_y", "body_acc_z", "body_gyro_x", "body_gyro_y", "body_gyro_z",
           "total_acc_x", "total_acc_y", "total_acc_z"]


def _inner(outer: bytes) -> zipfile.ZipFile:
    z = zipfile.ZipFile(io.BytesIO(outer))
    name = next((n for n in z.namelist() if n.endswith("UCI HAR Dataset.zip")), None)
    return zipfile.ZipFile(io.BytesIO(z.read(name))) if name else z


def _txt(z: zipfile.ZipFile, suffix: str) -> np.ndarray:
    name = next(n for n in z.namelist() if n.endswith(suffix))
    return np.loadtxt(io.BytesIO(z.read(name)))


def convert(raw_zip: bytes, out: Path) -> None:
    z = _inner(raw_zip)
    src_sha = hashlib.sha256(raw_zip).hexdigest()
    parts = {s: (_txt(z, f"{s}/X_{s}.txt"), _txt(z, f"{s}/y_{s}.txt").astype(int) - 1,
                 _txt(z, f"{s}/subject_{s}.txt").astype(int)) for s in ("train", "test")}
    raw = {s: np.stack([_txt(z, f"{s}/Inertial Signals/{sig}_{s}.txt") for sig in SIGNALS], axis=-1)
           for s in ("train", "test")}
    for kind in ("features", "raw"):
        d = out / f"uci_har_{kind}"
        d.mkdir(parents=True, exist_ok=True)
        X = np.concatenate([parts["train"][0], parts["test"][0]]) if kind == "features" else \
            np.concatenate([raw["train"], raw["test"]]).astype("float32")
        y = np.concatenate([parts["train"][1], parts["test"][1]])
        subj = np.concatenate([parts["train"][2], parts["test"][2]])
        origin = np.array(["train"] * len(parts["train"][1]) + ["test"] * len(parts["test"][1]))
        ids = np.array([f"{o}-{i}" for o, i in zip(origin, range(len(y)), strict=True)])
        np.savez(d / "data.npz", X=X, y=y, group=np.array([f"subj{s:02d}" for s in subj]), sample_id=ids,
                 official_split=origin)
        sha = hashlib.sha256((d / "data.npz").read_bytes()).hexdigest()
        (d / "manifest.json").write_text(json.dumps({
            "name": f"uci_har_{kind}", "version": "1.0", "source": URL, "source_sha256": src_sha,
            "license": "CC BY 4.0 (UCI dataset 240 page; owner must verify)", "group_key": "subject",
            "overlapping_windows": kind == "raw", "table": "data.npz",
            "official_test_subjects": sorted({f"subj{s:02d}" for s in parts["test"][2]}),
            "files": [{"path": "data.npz", "sha256": sha}]}, indent=1))
        print(f"{d}: {len(y)} windows, {X.shape}, subjects {len(set(subj))}")


def main() -> int:
    out = Path(sys.argv[1])
    if "--zip" in sys.argv:
        data = Path(sys.argv[sys.argv.index("--zip") + 1]).read_bytes()
    else:
        data = httpx.get(URL, timeout=300, follow_redirects=True).content
        (out / "uci_har_download.zip").parent.mkdir(parents=True, exist_ok=True)
        (out / "uci_har_download.zip").write_bytes(data)
    print("download sha256:", hashlib.sha256(data).hexdigest())
    convert(data, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
