# researchos-plugin-medimg_dicom 0.1.0

Local DICOM folder -> image dataset for Research OS (core contracts `>=1.7,<2`). **Research use only: not a medical
device, no diagnosis, no clinical deployment.** Imports only `researchos.contracts` and `researchos.sdk`.

## What
| Entry point | Group | What it builds |
|---|---|---|
| `medimg_dicom_folder` | `researchos.datasources` | One row per monochrome image frame: rescaled pixels (`X[N, rows, cols]`), label from a whitelisted attribute, pseudonymous patient group, pixel spacing |

## Access and privacy
- Capability `files:read` only: files are listed and read through the host's `ctx.local`, inside the owner's file
  roots with read access (symlinks refused). No network, no writes outside the build directory.
- PHI preflight (`deid.py`): counts non-empty PHI-bearing attributes (a subset of DICOM PS3.15 Annex E, Table E.1-1),
  private tags, overlays and BurnedInAnnotation. It reports keywords and counts, never values, and removes nothing.
- `phi_handling: "refuse"` (default) fails the build when the preflight finds anything; `"pseudonymize"` builds a
  pixels-only table: no header value is stored, groups and sample ids are HMAC pseudonyms with a per-build random
  salt that is discarded. BurnedInAnnotation = YES is always refused; absent flags on US/SC/OT/XC/ES/DOC are counted.
- The source path is not recorded (it may contain names); an aggregate SHA-256 of the file contents is.

## Pixels
- Decoded natively: uncompressed and RLE Lossless. Other compressed transfer syntaxes, non-monochrome images and
  objects without pixel data are skipped and counted in `extra["skipped"]`.
- `RescaleSlope` / `RescaleIntercept` applied; units: `RescaleType`, else HU for CT, else stated as unknown.
- `resize_to: [rows, cols]` resamples nearest-neighbour (values unchanged) and scales the pixel spacing; without it
  all images must share one shape.

## Settings (`dataset.build` payload `settings`)
`directory` (required), `license` (required: the data's terms, e.g. the TCIA collection licence), `phi_handling`,
`label_attribute` (`Modality` | `BodyPartExamined`), `max_total_mb` (2048), `max_decoded_mb` (1024, decoded pixels), `label_values` (required for BodyPartExamined: the closed list of
labels; other values become `other`), `accept_burned_in_unknown` (false), `resize_to`, `max_files` (2000),
`max_file_mb` (64). Header values never pass verbatim: labels, units (RescaleType defined terms) and the burned-in
flag use closed vocabularies; burned-in unknown on US/SC/OT/XC/ES/DOC refuses until the owner checked the pixels.
No split is suggested: plans choose a patient-level split (`group_counts`) on the pseudonymous groups.

## Tests
`pytest` (offline): pydicom's bundled test files (`pydicom/data/test_files`, MIT) copied into a temporary root;
synthetic edits (burned-in flag, removed PatientID) are made in the temporary copies only.
