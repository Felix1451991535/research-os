# Proposed additive contract changes

DatasetProvider, DatasetDescriptor, TemplatePackage and RunContext now exist and
are implemented. No core patches or substitute core protocols are shipped.

1. DatasetBuildContext offers an output directory and HTTP get, not streaming fetch,
   transactional publication, cache reuse or owner-selected data-root resolution.
   An additive host storage service could supply bounded streams and an opaque
   commit/abort handle. Current writes are confined to host staging and whole-build
   publication remains the host's responsibility.
2. DatasetDescriptor.extra can carry split metadata but has no typed subject split
   proposal/registration API. Add a versioned split proposal with algorithm, seed,
   grouping unit, subject lists and content hash. Current splits.json is an artifact;
   the plugin does not bypass host test/freeze controls or claim automatic activation.
3. RunContext has no resolved/approved device or artifact publication/fencing method.
   Add device/resource policy and declare_artifact(relative_path, mime, metadata).
   Current templates are CPU-only and write confusion_matrix.json in run_dir. The
   host must collect/review it; the plugin cannot manufacture lease guarantees.
4. Add cancellation/denied-split/lease test hooks to TemplateConformance; its current
   suite checks manifest and basic run semantics only. This package adds concrete
   pre-cancel, leakage and deterministic-metric tests independently.
5. Publish core's py.typed marker for external strict type checking.

PAMAP2 is optional and not implemented. Optional CUDA execution is deferred; no
GPU support, GPU performance, source-manifest verification or benchmark result is
claimed. Missing trusted checksums remain None, explicitly unverified per v2 rules.
