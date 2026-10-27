"""medimg_dicom_folder: a local DICOM folder (owner file root, via core 1.5 ctx.local) -> image dataset table.

Research use only. The table holds rescaled pixels, whitelisted technical attributes and salted pseudonyms; no
header value leaves the plugin. Default phi_handling "refuse" fails the build when the PHI preflight finds anything
(the error lists attribute keywords and counts only); "pseudonymize" is the owner's explicit choice and is recorded.
Burned-in annotation = YES is always refused (pixel-level PHI cannot be removed here).
"""

from __future__ import annotations

import hashlib
import io
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from researchos.contracts.datasources import DatasetBuildContext, DatasetDescriptor, DatasetFile, DataSourceSpec

from . import deid, images

VERSION = "0.1.0"
TABLE = "table.npz"
LABEL_ATTRIBUTES = ("Modality", "BodyPartExamined")
# PS3.3 C.7.3.1.1.1 Modality defined terms. Labels come only from closed vocabularies: header values outside them
# become "other", so no free text reaches labels, manifests or errors (review #42).
MODALITIES = (
    "AR", "ASMT", "AU", "BDUS", "BI", "BMD", "CR", "CT", "DG", "DOC", "DX", "ECG", "EPS", "ES", "FID", "GM", "HC",
    "HD", "IO", "IOL", "IVOCT", "IVUS", "KER", "KO", "LEN", "LS", "MG", "MR", "M3D", "NM", "OAM", "OCT", "OP", "OPM",
    "OPT", "OPTBSV", "OPTENF", "OPV", "OSS", "OT", "PLAN", "PR", "PT", "PX", "REG", "RESP", "RF", "RG", "RTDOSE",
    "RTIMAGE", "RTPLAN", "RTRECORD", "RTSTRUCT", "RWV", "SEG", "SM", "SMR", "SR", "SRF", "TG", "US", "VA", "XA", "XC",
)  # fmt: skip
SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["directory", "license"],
    "properties": {
        "directory": {"type": "string", "minLength": 1, "description": "absolute folder inside an owner read root"},
        "license": {"type": "string", "minLength": 1, "description": "data licence / terms, e.g. TCIA collection"},
        "phi_handling": {"enum": ["refuse", "pseudonymize"], "default": "refuse"},
        "label_attribute": {"enum": list(LABEL_ATTRIBUTES), "default": "Modality"},
        "label_values": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[A-Z0-9_ ]{1,16}$"},
            "minItems": 1,
            "uniqueItems": True,
            "description": "required for BodyPartExamined: the owner's closed list of labels",
        },
        "accept_burned_in_unknown": {
            "type": "boolean",
            "default": False,
            "description": "build although burned-in status is unknown on a risky modality (owner checked pixels)",
        },
        "resize_to": {
            "type": "array",
            "items": {"type": "integer", "minimum": 8, "maximum": 1024},
            "minItems": 2,
            "maxItems": 2,
        },
        "max_files": {"type": "integer", "minimum": 1, "maximum": 10000, "default": 2000},
        "max_file_mb": {"type": "integer", "minimum": 1, "maximum": 512, "default": 64},
        "max_decoded_mb": {
            "type": "integer",
            "minimum": 1,
            "maximum": 65536,
            "default": 1024,
            "description": "decoded pixels of all files together (compressed input can expand)",
        },
        "max_total_mb": {
            "type": "integer",
            "minimum": 1,
            "maximum": 16384,
            "default": 2048,
            "description": "all files together (everything is decoded in memory)",
        },
    },
    "additionalProperties": False,
}


class DicomBuildError(ValueError):
    """Refusal or invalid input; the message never contains header values."""


def _list(ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> list[str]:
    if ctx.local is None:
        raise DicomBuildError("the host granted no local read access (manifest capability files:read)")
    try:
        return ctx.local.list_files(str(settings["directory"]), limit=int(settings.get("max_files", 2000)))
    except (OSError, ValueError) as e:  # messages carry paths, which may contain names: never pass them on
        raise DicomBuildError(
            f"cannot list the folder ({type(e).__name__}: outside the roots, missing, or more than max_files files)"
        ) from None


def _read_all(ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> tuple[list[Any], Counter[str], str]:
    """Read and decode every listed file. Errors name a file by position only (paths may contain patient names);
    undecodable files are skipped and counted; max_file_mb and max_total_mb bound memory."""
    import pydicom
    from pydicom.errors import InvalidDicomError

    paths = _list(ctx, settings)
    max_bytes = int(settings.get("max_file_mb", 64)) * 1_000_000
    budget = int(settings.get("max_total_mb", 2048)) * 1_000_000
    skipped: Counter[str] = Counter()
    datasets, digests = [], []
    for i, path in enumerate(paths):
        if ctx.cancel_requested():
            raise DicomBuildError("build cancelled by request")
        try:
            data = ctx.local.read_bytes(path, max_bytes=max_bytes)  # type: ignore[union-attr]
        except (OSError, ValueError) as e:
            raise DicomBuildError(
                f"file {i + 1} of {len(paths)} could not be read ({type(e).__name__}; e.g. "
                "larger than max_file_mb, hard-linked, or changed while reading)"
            ) from None
        budget -= len(data)
        if budget < 0:
            raise DicomBuildError(f"the folder exceeds max_total_mb after {i + 1} of {len(paths)} files")
        digests.append(hashlib.sha256(data).hexdigest())
        try:
            ds = pydicom.dcmread(io.BytesIO(data))
        except InvalidDicomError:
            skipped["not_dicom"] += 1
        except Exception:  # noqa: BLE001 - truncated or malformed DICOM: counted, never shown (may echo content)
            skipped["unreadable_dicom"] += 1
        else:
            if _metadata_ok(ds):
                datasets.append(ds)
            else:
                skipped["malformed_metadata"] += 1
        ctx.progress("read", i + 1, len(paths))
    if not datasets:
        raise DicomBuildError(f"no DICOM files found ({len(paths)} files listed)")
    return datasets, skipped, hashlib.sha256("".join(sorted(digests)).encode()).hexdigest()


def _metadata_ok(ds: Any) -> bool:
    """Convert every element (nested too) and the values used later once, so a malformed value (e.g. text in an
    IS element) is skipped and counted here instead of raising later with its content in the message (review #63)."""
    try:
        for e in ds.iterall():
            str(e.value)
        int(ds.get("InstanceNumber", 0) or 0)
        images.spacing(ds)
        return True
    except Exception:  # noqa: BLE001 - any conversion failure: the file is not usable, and its text is not shown
        return False


def _check_phi(report: deid.Preflight, handling: str, accept_unknown: bool) -> None:
    summary, findings = report.summary(), report.findings()
    if findings.other["burned_in_yes"]:
        raise DicomBuildError(
            f"burned-in annotation = YES in {findings.other['burned_in_yes']} files: refused ({summary})"
        )
    if findings.uncertain.get("burned_in_invalid") or (
        findings.uncertain.get("burned_in_unknown_risky_modality") and not accept_unknown
    ):
        raise DicomBuildError(
            f"burned-in status unknown or invalid where text is common ({summary}); check the pixels, then set "
            "accept_burned_in_unknown=true (review #44)"
        )
    if handling == "refuse" and findings.found():
        raise DicomBuildError(
            f"PHI preflight found identifying attributes (phi_handling=refuse): {summary}. Set "
            "phi_handling=pseudonymize to build a pixels-only table with pseudonymous groups."
        )


def _decode(ds: Any, k: int, n: int, budget: int, used: int, skipped: Counter[str]) -> tuple[list[Any] | None, int]:
    """Frames of one object within the decoded-memory budget (review #64), or None with the reason counted."""
    try:
        need = images.decoded_bytes(ds)
        if used + need > budget:
            raise DicomBuildError(f"decoded pixels would exceed max_decoded_mb at file {k + 1} of {n}")
        return images.frames(ds), used + need
    except images.Skip as reason:
        skipped[str(reason)] += 1
    except DicomBuildError:
        raise
    except Exception:  # noqa: BLE001 - lazily decoded pixels of a truncated file: counted, never shown
        skipped["undecodable_pixels"] += 1
    return None, used


def _rows(datasets: list[Any], settings: Mapping[str, Any], skipped: Counter[str]) -> dict[str, Any]:
    ids = deid.EphemeralPseudonymizer()  # key discarded after the build: pseudonyms cannot be linked back
    attr = str(settings.get("label_attribute", "Modality"))
    allowed = MODALITIES if attr == "Modality" else tuple(settings.get("label_values") or ())
    if not allowed:
        raise DicomBuildError(f"label_attribute {attr} needs label_values: the closed list of labels to keep")
    shape = (int(settings["resize_to"][0]), int(settings["resize_to"][1])) if "resize_to" in settings else None
    rows: dict[str, list[Any]] = {k: [] for k in ("X", "label", "group", "sample_id", "spacing", "units")}
    order = sorted(datasets, key=lambda d: (str(d.get("SeriesInstanceUID", "")), int(d.get("InstanceNumber", 0) or 0)))
    budget, used = int(settings.get("max_decoded_mb", 1024)) * 1_000_000, 0
    for k, ds in enumerate(order):
        if not str(ds.get("PatientID", "")).strip():
            raise DicomBuildError("PatientID missing: patient-level groups (the leakage unit) cannot be formed")
        frames, used = _decode(ds, k, len(order), budget, used, skipped)
        if frames is None:
            continue
        group = ids.pseudonym("patient", str(ds.PatientID))
        series = ids.pseudonym("series", str(ds.get("SeriesInstanceUID", "")))
        row_mm, col_mm = images.spacing(ds)
        for f, img in enumerate(frames):
            out = images.resize(img, shape) if shape else img
            scale = (img.shape[0] / out.shape[0], img.shape[1] / out.shape[1])
            rows["X"].append(out)
            value = str(ds.get(attr, "")).strip().upper()
            rows["label"].append(value if value in allowed else ("unknown" if not value else "other"))
            rows["group"].append(group)
            rows["sample_id"].append(f"dcm/{series}/{k}/{f}")
            rows["spacing"].append((row_mm * scale[0], col_mm * scale[1]))
            rows["units"].append(images.units(ds))
    if not rows["X"]:
        raise DicomBuildError(f"no usable monochrome images (skipped: {dict(skipped)})")
    shapes = sorted({x.shape for x in rows["X"]})
    if len(shapes) > 1:
        raise DicomBuildError(f"images have different shapes {shapes[:5]}; set resize_to")
    return rows


def _write(rows: dict[str, Any], out_dir: Path) -> tuple[DatasetFile, dict[str, str]]:
    names = sorted(set(rows["label"]))
    arrays: dict[str, Any] = {
        "X": np.stack(rows["X"]).astype(np.float32),
        "y": np.array([names.index(v) for v in rows["label"]], dtype=np.int64),
        "group": np.asarray(rows["group"], dtype=str),
        "sample_id": np.asarray(rows["sample_id"], dtype=str),
        "pixel_spacing_mm": np.asarray(rows["spacing"], dtype=np.float64),
    }
    path = out_dir / TABLE
    np.savez(path, **arrays)
    return DatasetFile(TABLE, hashlib.sha256(path.read_bytes()).hexdigest()), {str(i): n for i, n in enumerate(names)}


class DicomFolderProvider:
    spec = DataSourceSpec(
        "medimg_dicom_folder",
        VERSION,
        "dataset",
        "Local DICOM folder (owner file root): rescaled monochrome pixels, pseudonymous patient groups, PHI preflight",
        hosts=(),
        settings_schema=SETTINGS_SCHEMA,
    )

    def build(self, ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> DatasetDescriptor:
        try:
            datasets, skipped, source_hash = _read_all(ctx, settings)
            report = deid.preflight(datasets)
            handling = str(settings.get("phi_handling", "refuse"))
            _check_phi(report, handling, bool(settings.get("accept_burned_in_unknown", False)))
            rows = _rows(datasets, settings, skipped)
            file, label_map = _write(rows, ctx.out_dir)
        except DicomBuildError:
            raise
        except Exception as e:  # noqa: BLE001 - provider boundary: library messages may echo paths or header text
            raise DicomBuildError(
                f"unexpected {type(e).__name__} while building (details withheld: they may contain identifiers)"
            ) from None
        extra = {
            "modality_units": sorted(set(rows["units"])),
            "x_layout": "X[N, rows, cols] float32 after RescaleSlope/Intercept; one row per frame",
            "pixel_spacing": "pixel_spacing_mm[N, 2] (row, col) after resizing; NaN when PixelSpacing is absent",
            "resize": {"to": settings.get("resize_to"), "method": "nearest neighbour by pixel centres"},
            "label_attribute": settings.get("label_attribute", "Modality"),
            "label_map": label_map,
            "phi_handling": handling,
            "phi_preflight": report.summary(),
            "pseudonyms": "HMAC-SHA256 with a per-build random salt that is discarded (group = patient)",
            "skipped": dict(sorted(skipped.items())),
            "n_samples": len(rows["X"]),
            "research_use_only": True,
        }
        return DatasetDescriptor(
            name="medimg_dicom_folder",
            version=VERSION,
            source="local DICOM folder (path not recorded: it may contain names)",
            license=str(settings["license"]),
            group_key="patient (pseudonymous)",
            table=TABLE,
            files=(file,),
            expected_source_sha256=None,
            observed_source_sha256=source_hash,
            extra=extra,
            egress="local_only",  # medical images never reach cloud models, whatever the licence (ADR-159)
        )
