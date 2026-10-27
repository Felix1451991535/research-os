"""Conformance for Importer: idempotent, hard-ID dedupe through the writer, rejections explained, no PDF stored
without an explicit open license."""

from __future__ import annotations

from typing import Any

from ...contracts.datasources import BibRecord
from ...contracts.importers import Importer, ImportItemResult
from ..testing import FakeHttp, InMemoryKnowledgeWriter

OPEN = ("CC-BY", "CC0", "CC-BY-SA", "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0", "PUBLIC-DOMAIN")


class ImporterConformance:
    def make_importer(self) -> Importer:
        raise NotImplementedError

    def sample_records(self) -> tuple[BibRecord, ...]:
        raise NotImplementedError

    def make_http(self) -> FakeHttp:
        raise NotImplementedError

    def settings(self) -> dict[str, Any]:
        return {}

    def _run(self, w: InMemoryKnowledgeWriter) -> list[ImportItemResult]:
        return self.make_importer().import_records(self.sample_records(), w, self.make_http(), self.settings())

    def test_one_result_per_record_with_reasons(self) -> None:
        res = self._run(InMemoryKnowledgeWriter())
        assert len(res) == len(self.sample_records())
        for r in res:
            assert r.status in ("created", "reused", "rejected")
            assert r.status != "rejected" or r.reason, "rejections must say why"
            assert r.status == "rejected" or r.work_id

    def test_idempotent(self) -> None:
        w = InMemoryKnowledgeWriter()
        first = self._run(w)
        works = dict(w.works)
        second = self._run(w)
        assert w.works == works, "re-import must not create works"
        assert [r.work_id for r in first] == [r.work_id for r in second]

    def test_no_original_without_open_license(self) -> None:
        w = InMemoryKnowledgeWriter()
        self._run(w)
        for _, _, lic in w.originals.values():
            assert lic is not None and lic.upper() in OPEN, f"stored a file with license {lic!r}"
