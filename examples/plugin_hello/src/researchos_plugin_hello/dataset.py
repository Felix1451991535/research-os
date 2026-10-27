"""Dataset provider: deterministic synthetic sensor windows (no network). Shows the build/verify contract."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from researchos.contracts.datasources import DatasetBuildContext, DatasetDescriptor, DatasetFile, DataSourceSpec


class SyntheticWindows:
    spec = DataSourceSpec("hello_synthetic", "0.1.0", "dataset", "Synthetic 3-class windows, 8 subjects")

    def build(self, ctx: DatasetBuildContext, settings: Mapping[str, Any]) -> DatasetDescriptor:
        import numpy as np

        rng = np.random.default_rng(int(settings.get("seed", 0)))
        X, y, g, ids = [], [], [], []
        for s in range(8):
            for c in range(3):
                for w in range(6):
                    X.append(rng.normal(c, 0.5, size=(16, 2)).astype("float32"))
                    y.append(c)
                    g.append(f"s{s}")
                    ids.append(f"s{s}-c{c}-w{w}")
            ctx.progress("subjects", s + 1, 8)
        np.savez(ctx.out_dir / "data.npz", X=np.stack(X), y=np.array(y), group=np.array(g), sample_id=np.array(ids))
        sha = hashlib.sha256((ctx.out_dir / "data.npz").read_bytes()).hexdigest()
        return DatasetDescriptor(
            "hello_synthetic",
            "0.1.0",
            "generated",
            "CC0-1.0",
            "subject",
            "data.npz",
            (DatasetFile("data.npz", sha),),
            extra={"channels": ["a", "b"]},
            egress="cloud_ok",  # synthetic CC0 data
        )
