"""Abstract conformance suites, one per extension point. A plugin's test module subclasses the suite and
implements the `make_*` hooks; pytest collects the subclass (name it Test...). The suites use plain asserts and
depend only on researchos.contracts / researchos.sdk, so they run fully offline.

    from researchos.sdk.conformance import BibliographicSourceConformance
    class TestMySource(BibliographicSourceConformance):
        def make_source(self): return MySource()
        def make_http(self): return FakeHttp({...recorded fixtures...}, MySource.spec.hosts)
        def sample_query(self): return SearchQuery("heart rate variability")
"""

from .datasource import BibliographicSourceConformance, DatasetProviderConformance
from .exporter import ExporterConformance
from .importer import ImporterConformance
from .template import TemplateConformance
from .tool import ToolConformance

__all__ = [
    "BibliographicSourceConformance",
    "DatasetProviderConformance",
    "ExporterConformance",
    "ImporterConformance",
    "TemplateConformance",
    "ToolConformance",
]
