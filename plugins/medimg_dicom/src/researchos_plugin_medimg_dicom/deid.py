"""De-identification boundary of medimg_dicom: PHI preflight (detection only) and pseudonyms. Implements the core 1.6
privacy ports: `DicomPhiInspector` (contracts.privacy.PhiInspector) and `EphemeralPseudonymizer` (Pseudonymizer).

The preflight reports which PHI-bearing attributes are non-empty, by keyword and file count, never their values. The
attribute list is a SUBSET of DICOM PS3.15 Annex E Table E.1-1 (Basic Application Level Confidentiality Profile),
plus private tags and overlays; it is not a complete Annex E implementation and it removes nothing. Data that leaves
this plugin never contains header values: only pixels, whitelisted technical attributes and salted pseudonyms.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from researchos.contracts.privacy import PhiFindings

REFERENCE = "DICOM PS3.15 Annex E, Table E.1-1 Basic Application Level Confidentiality Profile (subset)"
PHI_KEYWORDS = (
    "PatientName", "PatientID", "OtherPatientIDs", "OtherPatientNames", "PatientBirthDate", "PatientBirthTime",
    "PatientSex", "PatientAge", "PatientSize", "PatientWeight", "PatientAddress", "PatientTelephoneNumbers",
    "PatientMotherBirthName", "MedicalRecordLocator", "EthnicGroup", "Occupation", "AdditionalPatientHistory",
    "PatientComments", "AccessionNumber", "StudyID", "StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate",
    "StudyTime", "SeriesTime", "AcquisitionTime", "ContentTime", "ReferringPhysicianName", "PerformingPhysicianName",
    "OperatorsName", "PhysiciansOfRecord", "RequestingPhysician", "NameOfPhysiciansReadingStudy", "InstitutionName",
    "InstitutionAddress", "InstitutionalDepartmentName", "StationName", "DeviceSerialNumber", "StudyDescription",
    "SeriesDescription", "ImageComments", "StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID",
    "FrameOfReferenceUID", "RequestAttributesSequence", "PatientBirthName", "AcquisitionDateTime",
    "IssuerOfPatientID", "OtherPatientIDsSequence", "ReferencedPatientSequence",
)  # fmt: skip
PHI_SET = frozenset(PHI_KEYWORDS)
COVERAGE = "subset of PS3.15 Annex E Table E.1-1 (listed keywords), nested sequences included; private tags; overlays"
BURNED_IN_RISK_MODALITIES = ("US", "SC", "OT", "XC", "ES", "DOC")
BURNED_IN_VALUES = ("YES", "NO")  # anything else is counted as "invalid", never stored (review #42)


@dataclass
class Preflight:
    files: int = 0
    phi: Counter[str] = field(default_factory=Counter)
    private_tags_files: int = 0
    overlay_files: int = 0
    burned_in: Counter[str] = field(default_factory=Counter)  # YES / NO / absent / invalid (closed vocabulary)
    burned_in_unknown_risky: int = 0

    def add(self, ds: Any) -> None:
        """One record; every element is visited, including items of nested sequences (review #43). A keyword is
        counted once per record."""
        self.files += 1
        elements = list(ds.iterall())
        present = {e.keyword for e in elements if e.keyword in PHI_SET and str(e.value).strip()}
        self.phi.update(present)
        if any(e.tag.is_private for e in elements):
            self.private_tags_files += 1
        if any(0x6000 <= e.tag.group <= 0x601E and e.tag.group % 2 == 0 for e in elements):
            self.overlay_files += 1
        raw = str(ds.get("BurnedInAnnotation", "")).strip().upper()
        flag = raw if raw in BURNED_IN_VALUES else ("invalid" if raw else "absent")
        self.burned_in[flag] += 1
        if flag == "absent" and str(ds.get("Modality", "")) in BURNED_IN_RISK_MODALITIES:
            self.burned_in_unknown_risky += 1

    def findings(self) -> PhiFindings:
        """Port view: detected risks in `other`, risks not ruled out in `uncertain` (review #44)."""
        other = {
            "private_tags": self.private_tags_files,
            "overlays": self.overlay_files,
            "burned_in_yes": self.burned_in.get("YES", 0),
        }
        uncertain = {
            "burned_in_unknown_risky_modality": self.burned_in_unknown_risky,
            "burned_in_invalid": self.burned_in.get("invalid", 0),
        }
        return PhiFindings(self.files, dict(self.phi), other, REFERENCE, uncertain, COVERAGE)

    def summary(self) -> dict[str, Any]:
        """Keywords and counts only: safe to store in the manifest and to show in an error message."""
        return {
            "reference": REFERENCE,
            "files": self.files,
            "phi_attributes_nonempty": dict(sorted(self.phi.items())),
            "private_tags_files": self.private_tags_files,
            "overlay_files": self.overlay_files,
            "burned_in_annotation": dict(sorted(self.burned_in.items())),
            "burned_in_unknown_risky_modality": self.burned_in_unknown_risky,
            "coverage": COVERAGE,
        }


def preflight(datasets: Iterable[Any]) -> Preflight:
    report = Preflight()
    for ds in datasets:
        report.add(ds)
    return report


class DicomPhiInspector:
    def inspect(self, records: Iterable[Any]) -> PhiFindings:
        return preflight(records).findings()


class EphemeralPseudonymizer:
    """HMAC-SHA256 (64 bits) under a random per-instance key that is never stored; create one per build. Stable for
    the instance's life; no intended stability across instances - not anonymity (pixels can still link records).
    Namespace and value are length-prefixed, so no two (namespace, value) pairs share an encoding (review #45)."""

    linkable = False

    def __init__(self) -> None:
        self._key = secrets.token_bytes(16)

    def pseudonym(self, namespace: str, value: str) -> str:
        ns, v = namespace.encode(), value.encode()
        message = len(ns).to_bytes(8, "big") + ns + v
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()[:16]
