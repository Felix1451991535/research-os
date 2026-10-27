"""Pixel extraction for medimg_dicom: decode policy, modality rescale with units, frames, nearest-neighbour resize.

Decoded natively only: uncompressed (implicit/explicit VR, little/big endian) and RLE Lossless. Other compressed
syntaxes are skipped and counted, so the result never depends on optional decoders with differing licences.
"""

from __future__ import annotations

from typing import Any

import numpy as np

NATIVE_SYNTAXES = {
    "1.2.840.10008.1.2",  # Implicit VR Little Endian
    "1.2.840.10008.1.2.1",  # Explicit VR Little Endian
    "1.2.840.10008.1.2.2",  # Explicit VR Big Endian (retired)
    "1.2.840.10008.1.2.1.99",  # Deflated Explicit VR Little Endian
    "1.2.840.10008.1.2.5",  # RLE Lossless
}
MONOCHROME = ("MONOCHROME1", "MONOCHROME2")

Array = np.ndarray[Any, Any]


class Skip(Exception):  # noqa: N818  (a reason, not an error)
    """The object is not an image this provider takes; the reason is counted in the manifest."""


def decoded_bytes(ds: Any) -> int:
    """Memory the decoded float64 frames will take, from the header alone (before anything is decoded; review #64)."""
    if "PixelData" not in ds:
        raise Skip("no_pixel_data")
    frames_n = int(ds.get("NumberOfFrames", 1) or 1)
    return int(ds.Rows) * int(ds.Columns) * int(ds.get("SamplesPerPixel", 1) or 1) * frames_n * 8


def frames(ds: Any) -> list[Array]:
    """Rescaled frames (float64) of one image object; Skip with a countable reason otherwise."""
    if "PixelData" not in ds:
        raise Skip("no_pixel_data")
    syntax = str(ds.file_meta.get("TransferSyntaxUID", ""))
    if syntax not in NATIVE_SYNTAXES:
        raise Skip("compressed_syntax_not_decoded")
    if str(ds.get("PhotometricInterpretation", "")) not in MONOCHROME:
        raise Skip("not_monochrome")
    raw = np.asarray(ds.pixel_array, dtype=np.float64)
    stack = raw if int(ds.get("NumberOfFrames", 1) or 1) > 1 else raw[None, ...]
    slope, intercept = float(ds.get("RescaleSlope", 1) or 1), float(ds.get("RescaleIntercept", 0) or 0)
    return [frame * slope + intercept for frame in stack]


# PS3.3 C.11.1.1.2 defined terms; any other RescaleType is reported as "other", never verbatim (review #42)
RESCALE_TYPES = ("OD", "HU", "US", "MGML", "Z_EFF", "ED", "EDW", "HU_MOD", "PCT")


def units(ds: Any) -> str:
    """CT values after rescale are Hounsfield units (PS3.3 C.8.2.1); otherwise RescaleType when it is a defined
    term."""
    stated = str(ds.get("RescaleType", "")).strip().upper()
    if stated:
        return stated if stated in RESCALE_TYPES else "other (RescaleType not a defined term)"
    if str(ds.get("Modality", "")) == "CT" and "RescaleSlope" in ds:
        return "HU"
    return "stored value" if "RescaleSlope" not in ds else "rescaled value (RescaleType absent)"


def spacing(ds: Any) -> tuple[float, float]:
    """(row, column) pixel spacing in mm; NaN when PixelSpacing is absent (no calibration is assumed)."""
    ps = ds.get("PixelSpacing")
    return (float(ps[0]), float(ps[1])) if ps is not None and len(ps) == 2 else (float("nan"), float("nan"))


def resize(img: Array, shape: tuple[int, int]) -> Array:
    """Nearest-neighbour resampling by pixel centres (no smoothing, values unchanged)."""
    rows = np.minimum((np.arange(shape[0]) + 0.5) * img.shape[0] / shape[0], img.shape[0] - 1).astype(int)
    cols = np.minimum((np.arange(shape[1]) + 0.5) * img.shape[1] / shape[1], img.shape[1] - 1).astype(int)
    out: Array = img[np.ix_(rows, cols)]
    return out
