# Contributing to Research OS

Thanks for your interest! Issues and pull requests are welcome, in English or Chinese.

## Before you start
- For anything larger than a small fix, open an issue first so we can agree on the approach.
- Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The core contracts (K1–K9) are additive-only: removing,
  renaming or changing the meaning of anything needs an ADR in [docs/DECISIONS.md](docs/DECISIONS.md).
- New capabilities should usually be plugins (see [docs/PLUGIN_DEV.md](docs/PLUGIN_DEV.md)), not core changes.

## Development setup
```sh
uv sync --extra dev --extra experiments --extra plugins
(cd apps/web && pnpm install)
sh scripts/check.sh     # must print "ALL GATES PASSED" before you open a PR
```

## Code rules
- Small, focused functions and modules; no copy-paste duplication; reuse existing helpers.
- Type hints everywhere; `ruff` and `mypy` must be clean; import-linter layering must hold.
- Every behaviour change comes with tests; tests must be offline and deterministic (seed every random source).
- User-visible UI text goes through i18n (zh-CN and en key sets must stay identical).
- Never commit secrets, API keys, personal data or real patient data. Use obviously synthetic values in tests.

## Pull requests
- One topic per PR, with clear commit messages (e.g. `fix(advisor): ...`, `feat(web): ...`).
- Describe what changed, why, and how you tested it. Mark anything you could not test (e.g. Windows, GPU, iPad).

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
