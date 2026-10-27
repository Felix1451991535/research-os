"""PHI preflight counts keywords (never values), private tags, overlays and burned-in flags; pseudonyms are keyed."""

from __future__ import annotations

import json

import pydicom
from pydicom.data import get_testdata_file

from researchos_plugin_medimg_dicom import deid


def test_preflight_counts_without_values() -> None:
    ct = pydicom.dcmread(get_testdata_file("CT_small.dcm"))
    clean = pydicom.dcmread(get_testdata_file("CT_small.dcm"))
    for kw in deid.PHI_KEYWORDS:
        if kw in clean:
            delattr(clean, kw)
    for e in list(clean.iterall()):
        if e.tag.is_private:
            del clean[e.tag]
    marked = pydicom.dcmread(get_testdata_file("CT_small.dcm"))
    marked.add_new(0x00091001, "LO", "vendor note")
    marked.add_new(0x60003000, "OB", b"\x00\x01")
    marked.Modality, marked.BurnedInAnnotation = "US", "NO"
    report = deid.preflight([ct, clean, marked])
    s = report.summary()
    f = deid.DicomPhiInspector().inspect([ct, clean, marked])
    assert s["files"] == 3 and s["phi_attributes_nonempty"]["PatientName"] == 2 and f.found()
    assert f.attributes == s["phi_attributes_nonempty"] and f.other["overlays"] == 1 and f.records == 3
    assert s["private_tags_files"] >= 1 and s["overlay_files"] == 1
    assert s["burned_in_annotation"] == {"NO": 1, "absent": 2}
    assert "CompressedSamples" not in json.dumps(s)
    assert not deid.DicomPhiInspector().inspect([clean]).found()


def test_ports_are_implemented_and_pseudonyms_keyed() -> None:
    from researchos.contracts.privacy import PhiInspector, Pseudonymizer

    inspector: PhiInspector = deid.DicomPhiInspector()
    a: Pseudonymizer = deid.EphemeralPseudonymizer()
    b = deid.EphemeralPseudonymizer()
    assert inspector.inspect([]).records == 0 and a.linkable is False
    assert a.pseudonym("patient", "P1") == a.pseudonym("patient", "P1") != b.pseudonym("patient", "P1")
    assert a.pseudonym("patient", "P1") != a.pseudonym("series", "P1") != a.pseudonym("patient", "P2")
    assert len(a.pseudonym("patient", "P1")) == 16


SENTINEL = "SENTINEL_X9"


def test_nested_and_added_keywords_are_found() -> None:
    """Regression (review #43): a PatientName inside ReferencedPatientSequence and top-level PatientBirthName,
    AcquisitionDateTime, IssuerOfPatientID were missed."""
    from pydicom.dataset import Dataset

    ds = pydicom.dcmread(get_testdata_file("CT_small.dcm"))
    for kw in deid.PHI_KEYWORDS:
        if kw in ds:
            delattr(ds, kw)
    for e in list(ds.iterall()):
        if e.tag.is_private:
            del ds[e.tag]
    inner = Dataset()
    inner.PatientName = "SYNTHETIC^NAME"
    ds.ReferencedPatientSequence = [inner]
    ds.PatientBirthName, ds.AcquisitionDateTime, ds.IssuerOfPatientID = "X^Y", "20260101120000", "ISSUER"
    f = deid.DicomPhiInspector().inspect([ds])
    assert {
        "PatientName",
        "ReferencedPatientSequence",
        "PatientBirthName",
        "AcquisitionDateTime",
        "IssuerOfPatientID",
    } <= set(f.attributes) and f.attributes["PatientName"] == 1
    assert "nested" in f.coverage


def test_burned_in_vocabulary_is_closed_and_uncertainty_reported() -> None:
    """Regression (review #42, #44): an invalid flag value became a summary key; an unknown flag on US vanished."""
    bad = pydicom.dcmread(get_testdata_file("CT_small.dcm"))
    bad.BurnedInAnnotation = SENTINEL
    us = pydicom.dcmread(get_testdata_file("CT_small.dcm"))
    us.Modality = "US"
    report = deid.preflight([bad, us])
    assert SENTINEL not in json.dumps(report.summary()) and report.burned_in["invalid"] == 1
    f = report.findings()
    assert f.uncertain == {"burned_in_unknown_risky_modality": 1, "burned_in_invalid": 1} and not f.clear()


def test_namespace_encoding_has_no_collisions() -> None:
    """Regression (review #45): pseudonym("patient\\0x", "y") equalled pseudonym("patient", "x\\0y")."""
    p = deid.EphemeralPseudonymizer()
    assert p.pseudonym("patient\0x", "y") != p.pseudonym("patient", "x\0y")
    assert p.pseudonym("ab", "c") != p.pseudonym("a", "bc")
