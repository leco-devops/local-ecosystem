# Changelog

All notable changes to the **LEco DevOps Open Project** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- (none yet)

## [0.3.0] - 2026-05-16

### Added

- **Versioning system:** `VERSION`, `version.json`, `CHANGELOG.md`, per-release notes under `releases/`, and technical docs (`docs/VERSIONING.md`, `docs/RELEASE_NOTES.md`).
- **Dashboard `GET /api/version`** — platform version, component versions, and documentation links for automation and UI.
- **Overview · Updates & catalogs:** editable schedule (interval or fixed UTC times), mark-all-read with unread highlights, inline control-token entry.
- **Persistent auto-refresh** interval in the dashboard toolbar (localStorage).
- **Inline action preloaders** on buttons that trigger backend `fetch` calls (spinner + global preloader + tab progress).

### Changed

- Update-catalog watcher reads `ecosystem-stack/config/update-catalog-schedule.json` for sleep timing.

### Documentation

- Release notes and file manifests for v0.3.0, v0.2.0, v0.1.0 under `releases/`.
- Surfaced in dashboard **Docs** tab and Help **Further reading**.

## [0.2.0] - 2026-05-16

### Added

- **AirLLM** Docker service (`ecosystem-stack/airllm/`), Traefik routes, Infrastructure model manager, CLI `airllm` subcommands.
- **`leco-update-catalog`** background watcher: Docker Hub / Ollama / HuggingFace checks; generated catalogs and Help tables.
- **In-app Help & User Manual** (`/help`): hosting, developer's guide, architecture diagrams (Mermaid).
- Dashboard APIs: `/api/ecosystem/updates`, `/api/llm-catalog/ollama|airllm`, update-catalog panel on Overview.

### Changed

- Infrastructure tab: Ollama and AirLLM **Model manager** panels; jump navigation; full-page Help scroll.

## [0.1.0] - 2026-05-01

### Added

- **LEco DevOps** dashboard: Overview, Infrastructure, Metrics, Control, Hosted apps, Routes, Logs, Docs.
- **Ecosystem stack** orchestration (`ecosystem-stack/`), Traefik on `*.lh`, Ollama, Open WebUI, n8n, Postgres.
- **`leco-devops` CLI** (`tools/deploy-cli/`), hosting layout, registry, Traefik fragment merge.
- **Cloudflare-local** optional stack; AI-assisted onboarding; local edge runtimes for Workers-style apps.
- Foundation installer, `leco-cli.sh` unified entrypoint, hosted-app Traefik runbook.

[Unreleased]: https://github.com/rmaurya/local-ecosystem/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/rmaurya/local-ecosystem/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/rmaurya/local-ecosystem/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rmaurya/local-ecosystem/releases/tag/v0.1.0
