"""Checksummed dataset builds, confined to the host-owned staging directory."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import numpy as np
from researchos.contracts.datasources import DatasetBuildContext, DatasetDescriptor, DatasetFile, DataSourceSpec

from .archive import LIMIT, read_archive
from .parsers import ACTIVITIES, CHANNELS, subject_split, uci, uci_official_test_subjects, wisdm_with_stats


class UciHarProvider:
    spec = DataSourceSpec(
        "har_uci", "0.1.0", "dataset", "UCI HAR supplied 128-sample inertial windows", ("archive.ics.uci.edu",)
    )
    source_kind = "uci"

    def build(self, ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> DatasetDescriptor:
        url, version, license_name = (str(settings.get(k, "")) for k in ("source_url", "source_version", "license"))
        if not version or not license_name or not settings.get("license_source"):
            raise ValueError("Explicit source version, license and license_source are required")
        if urlsplit(url).scheme != "https" or urlsplit(url).hostname not in self.spec.hosts:
            raise ValueError("Source URL must use HTTPS on a declared host")
        expected = settings.get("source_sha256")
        if expected is not None and not re.fullmatch(r"[0-9a-f]{64}", str(expected)):
            raise ValueError("Expected checksum must be a lower-case SHA-256 or null")
        self._cancel(ctx)
        response = ctx.http.get(url)
        if response.status != 200 or len(response.body) > int(settings.get("max_source_bytes", LIMIT)):
            raise ValueError("Download failed or exceeds source size limit")
        if urlsplit(response.url).hostname not in self.spec.hosts or urlsplit(response.url).scheme != "https":
            raise ValueError("Unexpected response origin")
        observed = hashlib.sha256(response.body).hexdigest()
        if expected is not None and expected != observed:
            raise ValueError("Upstream checksum mismatch")
        self._cancel(ctx)
        files = read_archive(response.body, int(settings.get("max_expanded_bytes", LIMIT)))
        stats: dict[str, Any]
        if self.source_kind == "uci":
            arrays, stats = uci(files, observed), {"official_test_subjects": uci_official_test_subjects(files)}
        else:
            window, gap = int(settings.get("window_samples", 128)), int(settings.get("max_gap_ns", 150000000))
            arrays, counts = wisdm_with_stats(files, observed, window, gap)
            stats = {**counts}
        self._cancel(ctx)
        seed = int(settings.get("split_seed", 0))
        splits = subject_split(arrays["group"], seed)
        split_data = {
            "algorithm": "sorted-subject-permutation-v1",
            "seed": seed,
            "subjects": splits,
            "source_sha256": observed,
        }
        split_bytes = json.dumps(split_data, sort_keys=True, indent=2).encode()
        temp = ctx.out_dir / "data.tmp.npz"
        try:
            np.savez(temp, X=arrays["X"], y=arrays["y"], group=arrays["group"], sample_id=arrays["sample_id"])
            self._cancel(ctx)
            temp.replace(ctx.out_dir / "data.npz")
            (ctx.out_dir / "splits.json").write_bytes(split_bytes)
        finally:
            temp.unlink(missing_ok=True)
        outputs = tuple(
            DatasetFile(n, hashlib.sha256((ctx.out_dir / n).read_bytes()).hexdigest())
            for n in ("data.npz", "splits.json")
        )
        ctx.progress("windows", len(arrays["y"]), len(arrays["y"]))
        extra = {
            "channels": list(CHANNELS) if self.source_kind == "uci" else ["acc_x", "acc_y", "acc_z"],
            "units": ["g"] * 3 + ["rad/s"] * 3 + ["g"] * 3 if self.source_kind == "uci" else ["m/s^2"] * 3,
            "sampling_rate_hz": 50 if self.source_kind == "uci" else 20,
            "label_map": list(
                ("WALKING", "WALKING_UPSTAIRS", "WALKING_DOWNSTAIRS", "SITTING", "STANDING", "LAYING")
                if self.source_kind == "uci"
                else ACTIVITIES
            ),
            "license_source": str(settings["license_source"]),
            "source_version": version,
            "split_sha256": hashlib.sha256(split_bytes).hexdigest(),
            "checksum_status": "verified" if expected else "upstream checksum unverified",
            **stats,
        }
        return DatasetDescriptor(
            self.spec.name,
            version,
            url,
            license_name,
            "subject",
            "data.npz",
            outputs,
            self.source_kind == "uci",
            str(expected) if expected else None,
            observed,
            extra,
            egress=str(settings.get("egress", "local_only")),  # owner-stated; the license text is free-form
        )

    @staticmethod
    def _cancel(ctx: DatasetBuildContext) -> None:
        if ctx.cancel_requested():
            raise InterruptedError("Dataset build cancelled")


class WisdmProvider(UciHarProvider):
    spec = DataSourceSpec(
        "har_wisdm", "0.1.0", "dataset", "WISDM v1 accelerometer, discontinuity-safe windows", ("www.cis.fordham.edu",)
    )
    source_kind = "wisdm"
