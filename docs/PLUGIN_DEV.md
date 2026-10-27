# Building a Research OS plugin (core contracts 1.2)

A plugin is an ordinary Python package that depends on `researchos` but imports **only**
`researchos.contracts` (typed protocols/dataclasses, no I/O) and `researchos.sdk` (template runtime, offline test
doubles, conformance suites). Everything else in core is private and may change. You can build and fully test a
plugin offline: no running host, no network, no API keys.

Working reference: `examples/plugin_hello/` (one implementation of every extension point, strict-typed, 19 offline
conformance tests). Copy it to start.

## 1. Package layout and manifest
```
my-plugin/
  pyproject.toml          # entry points (below)
  src/researchos_plugin_x/__init__.py   # MANIFEST
  tests/                  # subclass the conformance suites
  fixtures/               # recorded or synthetic responses (label synthetic ones as such)
```
Every distribution declares exactly one manifest:
```toml
[project.entry-points."researchos.plugins"]
researchos_plugin_x = "researchos_plugin_x:MANIFEST"
```
```python
MANIFEST = PluginManifest(name="researchos_plugin_x", version="0.1.0", core_contract=">=1.2,<2",
                          capabilities=["network:api.crossref.org"])
```
The host refuses every entry of a distribution without a valid manifest or whose `core_contract` range excludes the
running core version. `capabilities` are the ceiling of what the host grants: a datasource's `spec.hosts` are
intersected with `network:<host>` capabilities. Installing/enabling a plugin requires owner approval.
`files:read` (core 1.5) gives a `DatasetProvider` `ctx.local` (`contracts.files.LocalReadContext`): read-only
listing and reading inside owner-configured file roots with read access (symlinks refused); without the capability
`ctx.local` is None. Tests use `sdk.testing.FakeLocalRead(root)`. There is no write counterpart.
Dataset providers set `DatasetDescriptor.egress` (core 1.7): `cloud_ok` only when the licence allows content to reach
cloud model providers; the default `local_only` keeps it on local models (the gateway's egress gate, ADR-159).
Tools set `ToolSpec.derived_from_args=True` (core 1.8) only when the output is a pure function of the call's
arguments; otherwise the output counts as possibly carrying stored data and the rest of the agent run stays on local
models (ADR-171).

Entry-point objects may be an **instance**, a **class** (instantiated once with no arguments) or a **zero-arg
factory** returning one object or a list.

## 2. Extension points
| Group | Object | Contract module | Conformance suite |
|---|---|---|---|
| `researchos.datasources` | `BibliographicSource` (`spec.kind="bibliographic"`, `search(query, http, settings)`) | `contracts.datasources` | `BibliographicSourceConformance` |
| `researchos.datasources` | `DatasetProvider` (`spec.kind="dataset"`, `build(ctx, settings)`) | `contracts.datasources` | `DatasetProviderConformance` |
| `researchos.importers` | `Importer` (`import_records(records, writer, http, settings)`) | `contracts.importers` | `ImporterConformance` |
| `researchos.importers` | `FileImporter` (`parse_file(req)`, pure: RIS/BibTeX/CNKI exports) | `contracts.importers` | (plain pytest) |
| `researchos.exporters` | `Exporter` (`export(snapshot, ctx, options)`) | `contracts.exporters` | `ExporterConformance` |
| `researchos.tools` | `ToolBinding(spec: ToolSpec, impl(args, ctx))` | `contracts.gateway` | `ToolConformance` |
| `researchos.templates` | `TemplatePackage(root)` → `template.json` + entry script using `researchos.sdk.RunContext` | `contracts.execution`, `sdk.runtime` | `TemplateConformance` |
Other groups (`task_kinds`, `approval_subjects`, `doc_types`, `routers` under `/api/v1/x/...`) exist for core-adjacent
plugins; see `contracts/plugins.py`.

## 3. Rules the host enforces (design for them)
- **Network**: only through the `HttpContext` you are given; https only; only granted hosts; private/loopback
  targets and redirects to them are refused; per-host rate limit, Retry-After, size cap, cache. Raises
  `HttpDenied` (never retried) or `HttpUnavailable`.
- **No invented data**: unknown metadata fields are `None` (not `""`), DOIs lower-case without URL prefix, no guessed
  years or licenses. Upstream dataset checksums come from settings/manifests; if you have none, return
  `expected_source_sha256=None` — the host records the dataset as "upstream checksum unverified".
- **Imports**: you never see approvals. The host checks a K3 approval bound to the hash of the exact selected
  records (`knowledge_import`) before calling you, consumes it once, and hands you a `KnowledgeWriter`
  (hard-identifier dedupe DOI > PMID > arXiv; title matches never merge; idempotency keys). Store PDFs only with
  an explicit open license.
- **Exports**: citations and numbers arrive as structured spans (`CiteSpan`, `MetricSpan`); resolve them through
  the `ExportContext`, never from text. In `final` mode an unresolved citation/metric must produce an `error`
  diagnostic. Write only inside `ctx.out_dir`; the host verifies paths and hashes every file.
- **Tools**: `ToolSpec` is the only input to permission decisions (`read_only`, `destructive`,
  `side_effect_scope`, `needs`); agents may only call read-only, side-effect-free tools; anything else must be an
  approved K4 effect. Tool output is untrusted data.
- **Templates**: the template directory must be **self-contained** — its code may import only the standard
  library, `researchos.sdk`, the packages listed in `requires` (import names such as `sklearn`, not distribution
  names), and modules inside the template directory. Never import your plugin package from a template: the
  owner-reviewed hash covers only the template directory, and the host gate refuses such templates
  (`template_not_self_contained`; checked by `TemplateConformance.test_self_contained`).
  Read data only via `ctx.data(split)` (logged) and `ctx.eval_split()`; never probe splits.
  Log finite metrics, `report()` headline metrics with a verification mode, `save_predictions()` with stable
  `sample_id`s for `recomputed` metrics, poll `ctx.cancel_requested()`. Plugin templates stay **unreviewed** until
  the owner records their hash in `<data>/config/REVIEWED.json` (`scripts/review_template.py`); a REVIEWED.json
  inside your plugin is ignored.

## 4. Testing offline
```python
from researchos.sdk.conformance import BibliographicSourceConformance
from researchos.sdk.testing import FakeHttp, json_response

class TestMySource(BibliographicSourceConformance):
    def make_source(self): return MySource()
    def make_http(self): return FakeHttp({API: json_response(API, FIXTURE)}, ("api.example.org",))
    def sample_query(self): return SearchQuery("heart rate variability")
```
`FakeHttp` refuses undeclared hosts and unrecorded URLs; `InMemoryKnowledgeWriter`, `FakeExportContext`,
`FakeBuildContext`, `FakeToolContext` mirror the host rules. Run `pytest`, `ruff`, `mypy --strict` (core ships `py.typed`, so strict typing follows the public surface).

## 5. Trying it in a host
`pip install -e my-plugin` into the Research OS venv, restart, then `GET /api/v1/extensions` lists accepted and
rejected entries. Endpoints: `POST /api/v1/sources/{name}/search`, `POST /api/v1/imports/{importer}` (after approving
`knowledge_import` for the returned hash), `POST /api/v1/imports/{importer}/parse-file`,
`POST /api/v1/exports/{exporter}`, task kind `dataset.build` (`{"provider": name}`), templates via plans.

## 6. Versioning
Contracts follow semver (core 1.3 = 1.0 + `TaskContext.run_id` (1.1) + these extension protocols (1.2) + the 1.3
additions below). Additive changes keep your `>=1.2,<2` range valid; declare `>=1.3,<2` if you use a 1.3 feature;
breaking changes require an ADR and a major version.

### core 1.4 additions (literature)
- Optional capabilities of a `BibliographicSource` (implement the methods on the same object; the host detects
  them): `CitationGraphSource.references/cited_by(record, http, settings, cursor, page_size)` for citation chaining,
  `RetractionSource.retraction_status(identifiers, http, settings)` (≤ 50 ids; unknown = `retracted=None`, never
  False).
- `OaResolver` (`spec.kind = "oa_resolver"`): `resolve(doi, http, settings) -> OaLocation | None`; the licence must
  come from an explicit upstream field (else None — the host then never stores the PDF).
- Reference implementations: `plugins/litsearch` (`lit_openalex` chaining + retractions, `lit_unpaywall`).

### core 1.3 additions
- `IdScheme` gains `zotero` (value `users/<id>/<key>` or `groups/<id>/<key>`): a library-scoped **hard** identity
  for the KnowledgeWriter, like DOI/PMID/arXiv.
- `contracts.gateway.ToolError(code, message)`: raise it for expected failures (e.g. invalid input); the host returns
  `ERROR [code]: message` to the model. Output larger than `ToolSpec.max_output_bytes` is rejected
  (`output_too_large`), never truncated. Any other exception fails only that step.
- **Secret settings**: mark a settings property `writeOnly: true` (or `format: "password"`, or
  `x-researchos-secret: true`). The owner sets it once (`PUT /api/v1/extensions/{group}/{name}/settings`); it is
  stored backend-only (secrets.env / env var), redacted from logs, never returned, and a request that carries it is
  refused (`secret_in_request`). Your entry receives the merged settings (stored + per-call non-secret overrides),
  validated against `spec.settings_schema` (else the manifest schema, or its per-entry sub-schema).
- `HttpContext` forwards `Range` and returns `206` responses; the 5 MB response cap applies per response, so fetch
  large files in ranges. Responses to credentialed requests are never cached.
