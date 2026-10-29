# Research OS

[中文说明](README.zh-CN.md)

Research OS is a personal, evidence-backed research workbench that runs on your own computer: a local Python
backend plus a web app (PWA) you open in the browser on the same machine, or on an iPad paired to it.

It covers one research loop end to end: **import documents → ask questions answered only from your library, with
every sentence linked to its source page → plan experiments → run them under control → diagnose results → write
the report**, with data accuracy and scientific rigor ranked above everything else.

> Status: early release (0.1.x). Built and tested on Linux; the Windows installer is compiled on Linux and its
> install on a real Windows machine has not been verified by the developer. GPU and iPad acceptance: not run.

## Features

- **Library and cited Q&A** – import PDFs and notes; answers quote your own documents and link each sentence to
  the page it came from.
- **Literature** – search Crossref, OpenAlex, PubMed, arXiv and Semantic Scholar; screening reviews; BibTeX / RIS /
  Zotero import.
- **Experiments and studies** – pre-registered experiment plans, seeded samplers (QMC, TPE, Gaussian-process
  search), stop rules, failure memory, ANOVA-based sensitivity analysis.
- **Writing** – document editor with a citation and number check; Markdown, Word, LaTeX and slide export.
- **Model routing** – bring your own API keys (OpenAI, Anthropic, DeepSeek, Zhipu or any OpenAI-compatible
  endpoint); keys stay on your machine, are redacted from logs and never returned by the API.
- **Plugins** – biomedical signal processing (ECG/EEG), DICOM imaging, diagnostic statistics, human-activity
  datasets, literature search, report export, Zotero import. See [docs/PLUGIN_DEV.md](docs/PLUGIN_DEV.md).
- **Bilingual UI** – Chinese / English, switchable at any time in Settings.
- **One-click update** – Settings → About checks the latest release (its release information must carry a valid
  Ed25519 signature), downloads the installer, verifies its sha256, installs it while keeping your data and rolls
  back if the new version does not start. Nothing is checked unless you click.

## Install (Windows)

1. Download `ResearchOS-Setup-<version>.exe` from the [Releases](../../releases) page.
2. Double-click it and click Next until Finish. No admin rights and no internet download are needed during install.
3. Open "Research OS" from the Desktop or Start Menu; it opens in your browser. On first start a one-time setup
   token is shown so you can create your account.

Your research data lives in the `data` folder of the install directory and is kept on update and, by default, on
uninstall. More details: [packaging/README_INSTALL.md](packaging/README_INSTALL.md).

## Run from source (Linux / macOS / Windows)

Requirements: Python 3.11–3.13, [uv](https://docs.astral.sh/uv/), Node.js 20+ and pnpm (for the web app).

```sh
uv sync --extra dev --extra experiments --extra plugins
(cd apps/web && pnpm install && pnpm build)
sh scripts/start.sh                 # Windows: scripts\start.ps1
```

Then open <http://127.0.0.1:8443>. Configuration and operations (data directory, model relay, proxies, backup and
restore): [docs/OPS.md](docs/OPS.md).

## Development

- Architecture and contracts: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); decisions: [docs/DECISIONS.md](docs/DECISIONS.md).
- All quality gates (ruff, mypy, import-linter, architecture health, plugins, pytest, web lint/typecheck/test/build):
  `sh scripts/check.sh`.
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md). Security reports: [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
