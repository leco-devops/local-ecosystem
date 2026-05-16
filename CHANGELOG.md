# Changelog

All notable changes to the **LEco DevOps Open Project** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **UI credential vault (local dev):** gitignored `config/ui-credentials.yaml`, registry JSON, Infrastructure **UI access** panel, hub actions, login-assist routes, and reset/apply for MinIO, MySQL, and PostgreSQL.
- **CF ↔ LEco service map:** `docs/CF_LECO_SERVICE_MAP.md`, `ecosystem-stack/config/cf-leco-service-registry.json`, Docs catalog entry, and cross-links across help/samples.
- **Control default policies:** per-target `start` / `stop` / `offloaded` with API, example JSON, and Infrastructure **Start stacks** shortcuts.
- **Infrastructure:** Cloudflare-local stack container table, Valkey probe, and `update-catalog` in ecosystem start order.
- **Hosted apps — attached services:** Per-app panel lists compose containers, edge runtimes, Cloudflare KV/R2/D1 bindings, and ecosystem data stores with inline credentials, connection strings, and management UI links (`attached_services` on snapshot API).
- **Attached services (Mongo/Redis):** Reads compose via `compose_tail` fallback, `.env` beside compose, and `MONGODB_URI` / `REDIS_URL` from app services; builds default `mongodb://` / host-port connection strings and mongo-express links when present.
- **Docs — attached services:** User help (`docs/help/12-hosted-app-attached-services.md`), developer API reference (`docs/help/dev-08-hosted-app-services.md`), and cross-links in LLD, playbook, DEPLOY_CLI, and user manual.

### Changed

- **Wrangler local bindings:** browser/hyperdrive/email documented as partial bridges; narrowed `productionOnlyBindings` defaults in Workers adapter.
- **MinIO:** `MINIO_BROWSER_REDIRECT_URL` so `s3.lh` browsers redirect to `minio-console.lh` instead of `:9001`.

### Fixed

- **Onboarding (multi-wrangler monorepos):** Detect and generate YAML now find `wrangler.*.toml` files (e.g. `infra/wrangler.api.toml`), emit multiple `infrastructure.runtimes[]` entries, and surface each Worker in register logs — not only a root `wrangler.toml`.
- **Register wizard:** Step 4 (Validate YAML) now marks complete after a successful validate; validation warns when wrangler files exist on disk but `leco.yaml` has empty `infrastructure`.
- **Workers-only deploy:** `dockerCompose.composeFile` no longer defaults to `docker-compose.yml` when only `composeFileFromManifest` / runtime overlay is set; runtime materialization sets `composeFileFromManifest: docker-compose.leco-runtime.yml` for Traefik-only stacks.
- **Host CLI paths:** `leco-devops deploy` from the workstation remaps materialized `source` symlinks (`/workspace-parent/...`) to sibling repos under the ecosystem parent and sets `LECO_ECOSYSTEM_ROOT` for runtime image build contexts.
- **Cloudflare Pages runtime:** `cloudflare-pages` adapter + `leco/runtime-cloudflare-pages` image (`wrangler pages dev`); detects `wrangler.pages.toml` / `infra/wrangler.*.pages*.toml`, auto-builds Vite output when `dist/` is missing, and routes `/` to the Pages runtime beside Worker `/api` routes.
- **Hosting config symlinks:** Materialize/register now mirrors every `infrastructure.runtimes[].config` and discovered `wrangler.*.toml` under `hosting/app-available/<slug>/`, remaps `/workspace-parent/...` on the host, and no longer skips updates when `Path.resolve()` follows symlinks outside staging.
- **UI access:** Reset & Apply recreates n8n Postgres DB (owners live in DB, not only `n8n_data`), provisions n8n/Open WebUI accounts (`Localdev1`), fixes MinIO console `MINIO_SERVER_URL` for server-side login, and improves magic-link/assist errors. n8n auto-login re-syncs owner via `user-management:reset` when vault password mismatches; Auto-login opens in a new tab.
- **Platform health:** container scan no longer stops at `leco-update-catalog` image 404 (false “missing” services).
- **Missing services list:** Operational Health now names which managed containers are down.
- **Attached services — MongoDB Compass:** Management link now opens `mongodb://127.0.0.1:<published-port>` (with credentials when known) instead of `mongodb://mongo:27017`, which only resolves inside the compose network.
- **Attached services — connection strings:** MongoDB, MySQL, PostgreSQL, Redis, and MinIO now show labeled **host** and **Docker DNS** endpoints (plus `*.lh` where applicable) instead of a single ambiguous URI list.

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
