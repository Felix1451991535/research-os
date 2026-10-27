"""Conformance for both providers and both experiment templates."""

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from researchos.contracts.datasources import DatasetProvider
from researchos.contracts.http import HttpResponse
from researchos.sdk.conformance import DatasetProviderConformance, TemplateConformance
from researchos.sdk.testing import FakeHttp

from researchos_plugin_har_datasets import np_package
from researchos_plugin_har_datasets.providers import UciHarProvider, WisdmProvider

FIX = Path(__file__).parents[1] / "fixtures"


class TestUci(DatasetProviderConformance):
    fixture = "uci.zip"
    url = "https://archive.ics.uci.edu/synthetic.zip"

    def make_provider(self) -> DatasetProvider:
        return UciHarProvider()

    def make_http(self) -> FakeHttp:
        return FakeHttp(
            {self.url: HttpResponse(200, {}, (FIX / self.fixture).read_bytes(), self.url)},
            self.make_provider().spec.hosts,
        )

    def settings(self) -> dict[str, Any]:
        return {
            "source_url": self.url,
            "source_version": "SYNTHETIC-v1",
            "license": "CC0-1.0",
            "license_source": "Original synthetic fixture",
            "window_samples": 4,
            "source_sha256": hashlib.sha256((FIX / self.fixture).read_bytes()).hexdigest(),
        }


class TestWisdm(TestUci):
    fixture = "wisdm.zip"
    url = "https://www.cis.fordham.edu/synthetic.zip"

    def make_provider(self) -> DatasetProvider:
        return WisdmProvider()


def arrays(groups: list[str], seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    n = len(groups) * 6
    y = np.arange(n) % 3
    return {
        "X": (rng.normal(0, 0.1, (n, 8, 3)) + y[:, None, None]).astype("float32"),
        "y": y,
        "group": np.repeat(groups, 6),
        "sample_id": np.array([f"{g}-{i}" for g in groups for i in range(6)]),
    }


class TestRF(TemplateConformance):
    def template_root(self) -> Path:
        return np_package().root

    def params(self) -> dict[str, Any]:
        return {"model": "rf", "trees": 5, "n_classes": 3}

    def split_arrays(self) -> dict[str, dict[str, Any]]:
        return {"train": arrays(["s1", "s2", "s3"], 0), "search_val": arrays(["s4", "s5"], 1)}


class TestCNN(TestRF):
    def params(self) -> dict[str, Any]:
        return {"model": "cnn", "epochs": 3, "n_classes": 3}
