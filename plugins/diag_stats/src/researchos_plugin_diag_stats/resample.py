"""Seeded cluster (subject) resampling shared by stats_cluster_bootstrap and the Fleiss kappa CI.

Resample r draws G cluster indices with replacement via np.random.default_rng(seed).integers(0, G, size=G),
one call per resample in order, so results are bit-for-bit reproducible for the same seed, input and numpy
version, independent of chunking.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
from numpy.typing import NDArray

from ._common import interval

REFERENCE = (
    "Efron B, Tibshirani RJ (1993) An Introduction to the Bootstrap, ch. 13 (percentile interval); "
    "Field CA, Welsh AH (2007) J R Stat Soc B 69:369-390 (cluster bootstrap)"
)
WORK_CELLS = 2_000_000  # elements per chunk (memory bound)


def weights(seed: int, n_units: int, resamples: int, row_cost: int) -> Iterator[NDArray[np.int64]]:
    """Yield (chunk, n_units) multiplicity matrices; row r counts how often each unit was drawn in resample r."""
    rng = np.random.default_rng(seed)
    chunk = max(1, min(resamples, WORK_CELLS // max(1, row_cost)))
    done = 0
    while done < resamples:
        c = min(chunk, resamples - done)
        draws = np.stack([rng.integers(0, n_units, size=n_units) for _ in range(c)])
        offsets = draws + (np.arange(c, dtype=np.int64) * n_units)[:, None]
        yield np.bincount(offsets.ravel(), minlength=c * n_units).reshape(c, n_units).astype(np.int64)
        done += c


def percentile_ci(values: NDArray[np.float64], confidence: float) -> list[float] | None:
    """Percentile interval over the defined (finite) resample values; None when none is defined."""
    ok = values[np.isfinite(values)]
    if ok.size == 0:
        return None
    a = (1.0 - confidence) / 2.0
    lo, hi = np.quantile(ok, [a, 1.0 - a])
    return interval(float(lo), float(hi))


def summary(values: NDArray[np.float64], seed: int, resamples: int, confidence: float) -> dict[str, object]:
    defined = int(np.isfinite(values).sum())
    return {
        "ci": percentile_ci(values, confidence),
        "seed": seed,
        "resamples": resamples,
        "resamples_defined": defined,
        "resamples_undefined": resamples - defined,
        "interval": "percentile",
        "quantile_method": "numpy linear (type 7)",
    }
