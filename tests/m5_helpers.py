"""M5 helpers: in-test bibliographic sources (no plugin dependency, no network) registered on the live services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from researchos.contracts.datasources import (
    BibRecord,
    DataSourceSpec,
    OaLocation,
    RetractionStatus,
    SearchPage,
    SearchQuery,
)
from researchos.contracts.http import HttpUnavailable
from researchos.contracts.plugins import PluginManifest
from researchos.services.container import Services


def rec(
    source: str, sid: str, title: str, doi: str | None = None, kind: str = "article", year: int = 2024
) -> BibRecord:
    return BibRecord(
        source,
        sid,
        title,
        ("A. Author",),
        year,
        "Synthetic Journal",
        f"Abstract of {title}",
        (("doi", doi),) if doi else (),
        kind=kind,
    )


@dataclass
class FakeSource:
    name: str
    hits: dict[str, list[BibRecord]] = field(default_factory=dict)  # query text -> records
    graph: dict[str, list[BibRecord]] = field(default_factory=dict)  # "cited_by:<doi>" -> records
    retracted: dict[str, bool] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    queries: list[SearchQuery] = field(default_factory=list)

    @property
    def spec(self) -> DataSourceSpec:
        return DataSourceSpec(self.name, "0", "bibliographic", "fake", ("api.example.org",))

    def search(self, query: SearchQuery, http: Any, settings: Mapping[str, Any]) -> SearchPage:
        self.calls.append(query.text)
        self.queries.append(query)
        if query.text == "fail":
            raise HttpUnavailable("upstream down (synthetic)")
        found = self.hits.get(query.text, [])
        return SearchPage(tuple(found), None, len(found))

    def _g(self, key: str, record: BibRecord) -> SearchPage:
        d = dict(record.identifiers).get("doi")
        return SearchPage(tuple(self.graph.get(f"{key}:{d}", [])))

    def references(
        self, record: BibRecord, http: Any, settings: Any, cursor: str | None = None, page_size: int = 25
    ) -> SearchPage:
        return self._g("references", record)

    def cited_by(
        self, record: BibRecord, http: Any, settings: Any, cursor: str | None = None, page_size: int = 25
    ) -> SearchPage:
        return self._g("cited_by", record)

    def retraction_status(self, identifiers: Any, http: Any, settings: Any) -> tuple[RetractionStatus, ...]:
        return tuple(
            RetractionStatus(
                i, self.retracted.get(i[1]), self.name, f"https://doi.org/{i[1]}" if self.retracted.get(i[1]) else None
            )
            for i in identifiers
        )


@dataclass
class FakeOa:
    locations: dict[str, OaLocation] = field(default_factory=dict)

    @property
    def spec(self) -> DataSourceSpec:
        return DataSourceSpec("fake_oa", "0", "oa_resolver", "fake", ("api.example.org",))

    def resolve(self, doi: str, http: Any, settings: Any) -> OaLocation | None:
        return self.locations.get(doi)


def register(svc: Services, *objs: Any) -> None:
    m = PluginManifest(
        name="fake_plugin", version="0.1.0", core_contract=">=1.4,<2", capabilities=["network:api.example.org"]
    )
    for o in objs:
        table = svc.extensions.oa_resolvers if o.spec.kind == "oa_resolver" else svc.extensions.sources
        table[o.spec.name] = o
        svc.extensions.owners[f"datasources:{o.spec.name}"] = m
