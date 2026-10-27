"""physio_synth_eeg: SYNTHETIC motor-imagery EEG with known, planted truth (ADR-187; approved addition).
For checking analysis pipelines - recovery of planted effects, per-subject results, artifact detection - never for
claims about real EEG. Deterministic for a given seed; no network.

Sensor-space generator (no head/forward model): per channel 1/f ("pink") background, spatially mixed between
neighbouring electrodes; a mu rhythm (subject-specific 9-12 Hz) over each sensorimotor hemisphere. Planted effect:
imagining the left fist suppresses the right-hemisphere mu amplitude (C4 area) by `erd` and vice versa
(event-related desynchronisation, contralateral). `non_responders` (a fraction of subjects) carry no effect at all.
Planted artifacts: a large slow frontal deflection in `artifact_fraction` of the windows. Everything planted is
listed in the manifest (`known_truth`): responders, non-responders, artifact sample ids, effect size and frequency.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import numpy as np
from researchos.contracts.datasources import DatasetBuildContext, DatasetDescriptor, DataSourceSpec

from .common import Table, check_cancel, class_counts, seeded_partition, split_spec

VERSION = "1.0.0"
CHANNELS = ("FC3", "C3", "CP3", "CZ", "FC4", "C4", "CP4")
LEFT, RIGHT = (0, 1, 2), (4, 5, 6)  # electrode rows over each hemisphere; C3 = 1, C4 = 5
MIX = 0.3  # share of each neighbour's background mixed into a channel (volume conduction stand-in)
SETTINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subjects": {"type": "integer", "minimum": 4, "maximum": 60, "default": 12},
        "trials_per_class": {"type": "integer", "minimum": 5, "maximum": 200, "default": 20},
        "sampling_rate_hz": {"enum": [128, 160, 250], "default": 160},
        "window_s": {"type": "number", "minimum": 1.0, "maximum": 4.0, "default": 3.0},
        "erd": {"type": "number", "minimum": 0, "maximum": 0.9, "default": 0.5},
        "non_responders": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.25},
        "artifact_fraction": {"type": "number", "minimum": 0, "maximum": 0.3, "default": 0.05},
        "seed": {"type": "integer", "minimum": 0, "default": 0},
        "val_fraction": {"type": "number", "minimum": 0, "maximum": 0.4},
        "test_fraction": {"type": "number", "minimum": 0, "maximum": 0.5},
    },
    "additionalProperties": False,
}


def pink(rng: np.random.Generator, n: int, size: int) -> np.ndarray[Any, Any]:
    """n independent unit-variance 1/f noise series (spectrum shaped in the frequency domain)."""
    spec = np.fft.rfft(rng.normal(size=(n, size)), axis=1)
    f = np.arange(spec.shape[1])
    spec[:, 1:] /= np.sqrt(f[1:])
    spec[:, 0] = 0
    x = np.fft.irfft(spec, n=size, axis=1)
    return np.asarray(x / x.std(axis=1, keepdims=True))


def _mix(bg: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    out = bg.copy()
    for side in (LEFT, RIGHT):
        for i in side:
            out[i] += MIX * sum(bg[j] for j in side if j != i)
    return out


def trial(rng: np.random.Generator, label: int, fs: int, samples: int, subj: dict[str, Any]) -> np.ndarray[Any, Any]:
    """One window in microvolts. label 0 = left fist (right-hemisphere ERD), 1 = right fist (left-hemisphere ERD)."""
    t = np.arange(samples) / fs
    x = 10.0 * _mix(pink(rng, len(CHANNELS), samples))
    phase = rng.uniform(0, 2 * np.pi, 2)
    for k, side in enumerate((LEFT, RIGHT)):
        suppressed = (label == 1 and k == 0) or (label == 0 and k == 1)
        amp = 12.0 * (1 - subj["erd"] if suppressed else 1.0)
        mu = amp * np.sin(2 * np.pi * subj["mu_hz"] * t + phase[k])
        for i, w in zip(side, (0.5, 1.0, 0.5), strict=True):  # strongest at C3 / C4
            x[i] += w * mu
    return np.asarray(x * subj["gain"])


def _subjects(rng: np.random.Generator, n: int, erd: float, nonresp: float) -> dict[str, dict[str, Any]]:
    names = [f"SYN{i + 1:03d}" for i in range(n)]
    off = set(rng.choice(names, size=int(round(nonresp * n)), replace=False).tolist())
    return {
        s: {
            "erd": 0.0 if s in off else erd,
            "mu_hz": float(rng.uniform(9, 12)),
            "gain": float(rng.uniform(0.8, 1.25)),
            "responder": s not in off,
        }
        for s in names
    }


class SynthEegProvider:
    spec = DataSourceSpec(
        "physio_synth_eeg",
        "0.1.0",
        "dataset",
        "SYNTHETIC motor-imagery EEG with planted, recorded truth (contralateral mu ERD, non-responders, artifacts); "
        "for pipeline checks only",
        hosts=(),
        settings_schema=SETTINGS_SCHEMA,
    )

    def build(self, ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> DatasetDescriptor:
        seed = int(settings.get("seed", 0))
        rng = np.random.default_rng(seed)
        fs = int(settings.get("sampling_rate_hz", 160))
        samples = int(round(float(settings.get("window_s", 3.0)) * fs))
        subs = _subjects(
            rng,
            int(settings.get("subjects", 12)),
            float(settings.get("erd", 0.5)),
            float(settings.get("non_responders", 0.25)),
        )
        per_class, art = int(settings.get("trials_per_class", 20)), float(settings.get("artifact_fraction", 0.05))
        table, artifacts = Table(), []
        for n, (s, subj) in enumerate(subs.items()):
            check_cancel(ctx)
            for k in range(2 * per_class):
                label = k % 2
                x = trial(rng, label, fs, samples, subj)
                sid = f"synth/{s}/{k}"
                if rng.random() < art:  # slow frontal deflection, ~150-300 uV
                    bump = rng.uniform(150, 300) * np.hanning(samples)
                    x[0] += bump
                    x[4] += bump
                    artifacts.append(sid)
                table.add(x, label, s, sid, sampling_rate_hz=float(fs))
            ctx.progress("subjects", n + 1, len(subs))
        val, test = float(settings.get("val_fraction", 0.15)), float(settings.get("test_fraction", 0.2))
        assign = seeded_partition(table.group, {"test": test, "search_val": val, "confirm_val": val}, seed)
        file = table.write(ctx.out_dir)
        label_map = {0: "left_fist", 1: "right_fist"}
        truth = {
            "effect": "contralateral mu (event-related desynchronisation) amplitude reduction",
            "erd": float(settings.get("erd", 0.5)),
            "responders": sorted(s for s, v in subs.items() if v["responder"]),
            "non_responders": sorted(s for s, v in subs.items() if not v["responder"]),
            "mu_hz": {s: round(v["mu_hz"], 3) for s, v in subs.items()},
            "artifact_sample_ids": artifacts,
            "informative": "mu-band power at C3/C4 (and neighbours) of responders; nothing else depends on the label",
        }
        settings_hash = hashlib.sha256(repr(sorted(settings.items())).encode()).hexdigest()
        return DatasetDescriptor(
            name="physio_synth_eeg",
            version=VERSION,
            source=f"synthetic:physio_synth_eeg@{VERSION}?settings={settings_hash[:16]}",
            license="CC0-1.0",
            group_key="subject",
            table=file.path,
            files=(file,),
            egress="cloud_ok",
            extra={
                "dataset": "SYNTHETIC motor-imagery EEG (generated; not real recordings)",
                "synthetic": True,
                "generator": "sensor-space: 1/f background + mu rhythm + planted contralateral ERD; no forward model",
                "signal_type": "EEG",
                "sampling_rate_hz": fs,
                "channels": list(CHANNELS),
                "units": "uV",
                "window_s": samples / fs,
                "x_layout": "X[N, channels, window_samples]; sampling_rate_hz[N]",
                "label_map": {str(k): v for k, v in label_map.items()},
                "class_counts": class_counts(table.y, label_map),
                "known_truth": truth,
                "settings": dict(settings),
                "split": split_spec(assign, table.group),
                "split_source": "seeded subject-level partition",
                "n_samples": len(table.y),
            },
        )
