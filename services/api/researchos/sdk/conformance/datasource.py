"""Conformance for BibliographicSource and DatasetProvider."""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any

from ...contracts.datasources import (
    WINDOW_TABLE_ARRAYS,
    BibliographicSource,
    BibRecord,
    DatasetDescriptor,
    DatasetProvider,
    SearchPage,
    SearchQuery,
)
from ...contracts.http import HttpDenied
from ..testing import FakeBuildContext, FakeHttp, FakeLocalRead

NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def check_spec_name(name: str) -> None:
    assert NAME.match(name), f"spec.name {name!r} must match {NAME.pattern}"


class BibliographicSourceConformance:
    def make_source(self) -> BibliographicSource:
        raise NotImplementedError

    def make_http(self) -> FakeHttp:
        raise NotImplementedError

    def sample_query(self) -> SearchQuery:
        raise NotImplementedError

    def settings(self) -> dict[str, Any]:
        return {}

    def _search(self) -> SearchPage:
        return self.make_source().search(self.sample_query(), self.make_http(), self.settings())

    def test_spec(self) -> None:
        s = self.make_source().spec
        check_spec_name(s.name)
        assert s.kind == "bibliographic" and s.version and s.description

    def test_returns_normalized_records(self) -> None:
        page = self._search()
        assert isinstance(page, SearchPage) and page.records, "fixture query must return at least one record"
        src = self.make_source().spec.name
        for r in page.records:
            assert isinstance(r, BibRecord) and r.source == src and r.source_id and r.title.strip()
            for f in fields(BibRecord):  # unknown = None, never empty-string placeholders
                v = getattr(r, f.name)
                assert v != "", f"{f.name} must be None when unknown, not ''"
            for scheme, value in r.identifiers:
                if scheme == "doi":
                    assert value == value.lower() and not value.startswith(("http", "doi:")), value
                if scheme == "arxiv":
                    assert not value.lower().startswith("arxiv:"), value
            assert r.year is None or 1500 < r.year < 2200

    def test_deterministic(self) -> None:
        assert self._search() == self._search()

    def test_only_declared_hosts(self) -> None:
        src = self.make_source()
        http = self.make_http()
        http.allowed_hosts = tuple(h for h in http.allowed_hosts if h in src.spec.hosts)
        try:
            src.search(self.sample_query(), http, self.settings())
        except HttpDenied as e:  # pragma: no cover - failure path
            raise AssertionError(f"source contacted an undeclared host: {e}") from e


class DatasetProviderConformance:
    def make_provider(self) -> DatasetProvider:
        raise NotImplementedError

    def make_http(self) -> FakeHttp:
        raise NotImplementedError

    def settings(self) -> dict[str, Any]:
        return {}

    def make_local(self) -> FakeLocalRead | None:
        """core 1.5: providers declaring files:read return a FakeLocalRead over their test files."""
        return None

    def _build(self, out: Path) -> DatasetDescriptor:
        p = self.make_provider()
        return p.build(FakeBuildContext(out, self.make_http(), local=self.make_local()), self.settings())

    def test_spec(self) -> None:
        s = self.make_provider().spec
        check_spec_name(s.name)
        assert s.kind == "dataset"

    def test_build_is_verifiable_and_confined(self) -> None:
        import numpy as np

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            d = self._build(out)
            assert d.license and d.source and d.group_key
            for f in d.files:
                p = (out / f.path).resolve()
                assert p.is_relative_to(out.resolve()) and p.is_file(), f"file outside build dir or missing: {f.path}"
                assert hashlib.sha256(p.read_bytes()).hexdigest() == f.sha256, f"hash mismatch {f.path}"
            assert d.table in {f.path for f in d.files}
            with np.load(out / d.table, allow_pickle=False) as npz:  # closed before cleanup (WinError 32)
                t = {k: npz[k] for k in npz.files}
            for a in WINDOW_TABLE_ARRAYS:
                assert a in t, f"table lacks array {a}"
            n = len(t["y"])
            assert len(t["X"]) == n == len(t["group"]) == len(t["sample_id"])
            assert len(set(t["sample_id"].tolist())) == n, "sample_id must be unique"
            assert d.expected_source_sha256 is None or d.observed_source_sha256 == d.expected_source_sha256, (
                "observed upstream checksum differs from the expected one"
            )
