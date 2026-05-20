# Changelog

All notable changes to the **LEco DevOps Open Project** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **README (GitHub Pages):** Brand-style landing — USP (read/convert/orchestrate/deploy), dedicated **Platform** section, Features and use cases; removed in-repo GitHub Pages setup instructions; technical detail in [docs/PROJECT.md](docs/PROJECT.md).

### Added

- **Platform READMEs:** [platform/README.md](platform/README.md) and [platform/dev-stacks/README.md](platform/dev-stacks/README.md) document dev stack layout (parallel to [hosting/app-available/README.md](hosting/app-available/README.md)).

### Changed

- **Contributor attribution:** README and [OPEN_SOURCE.md](docs/OPEN_SOURCE.md) clarify that the `leco-devops` org hosts the official repo while commits and pushes come from individual GitHub accounts (primary: [@rmaurya](https://github.com/rmaurya)).

- **Official repository:** Canonical GitHub home is [leco-devops/local-ecosystem](https://github.com/leco-devops/local-ecosystem) (README, docs, update watcher, changelog compare links).

- **README & governance copy:** Project-first landing (no company header); community ownership language; Techtonic Systems Media And Research LLC framed as operational steward only ([OPEN_SOURCE.md](docs/OPEN_SOURCE.md), [CONTRIBUTING.md](CONTRIBUTING.md)).

- **Steward branding:** Use legal name **Techtonic Systems Media And Research LLC** and [https://techtonic.systems/](https://techtonic.systems/) across docs, LICENSE, NOTICE, dashboard footer, and README (GitHub Pages contributor CTA).

- **Contact & contributors:** Project email [leco@techtonic.systems](mailto:leco@techtonic.systems); top contributor credit for Rajneesh Maurya ([GitHub](https://github.com/rmaurya), [LinkedIn](https://www.linkedin.com/in/rajneeshmaurya/)).

- **Open source stewardship:** Project documentation, [LICENSE](LICENSE), [NOTICE](NOTICE.md), and root [README](README.md) (GitHub Pages landing) now identify **LEco DevOps Open Project** as open source managed by **[Techtonic Systems Media And Research LLC](https://techtonic.systems/)**. Full technical guide moved to [docs/PROJECT.md](docs/PROJECT.md).

- **Platform documentation:** New Help manual [docs/help/03-platform-tab.md](docs/help/03-platform-tab.md) (dev stack builder, Repair/Reinstall/Destroy, stack cards, cloud VM). Updated [DEV_STACK_ISOLATION.md](docs/DEV_STACK_ISOLATION.md), [CLOUD_VM_DEPLOYMENT.md](docs/CLOUD_VM_DEPLOYMENT.md), [LECO_USER_MANUAL.md](docs/LECO_USER_MANUAL.md), and developer API guide [docs/help/dev-09-platform-cloud.md](docs/help/dev-09-platform-cloud.md).

- **CLI — platform & dev stacks:** `leco-devops platform` (show, catalog, presets, services, traefik-apply, bind) and `leco-devops dev-stack` (create, start/stop, repair, reinstall, destroy, snapshot, access, logs) call the same dashboard modules as the Platform tab. Documented in [DEPLOY_CLI.md](docs/DEPLOY_CLI.md) and [tools/deploy-cli/README.md](tools/deploy-cli/README.md).

### Fixed

- **Platform — dev stack builder:** Builder form is a collapsible panel (closed by default); **Your dev stacks** list stays visible outside it.

- **Dev stack public URLs:** WordPress, WooCommerce, Ghost, Joomla (sample auto-install), and Magento templates bind to `http://{stackId}.lh` (not `localhost`); **Start** waits for `wp-sample-init` / `wp core is-installed` before URL repair (no more `wp core install` spam in logs); Platform tab shows line-oriented compose output in a scrollable log panel.

- **Dev stack Destroy:** `compose down -v --remove-orphans`, prunes leftover project containers/volumes/networks, removes stack files only after compose succeeds, updates platform config and Traefik routes; confirmation dialog in the Platform tab.

- **Dev stack live logs:** Start/Stop/Destroy stream NDJSON via `/api/dev-stacks/<id>/action/stream` (compose output and `wp-sample-init` logs in real time); Platform tab log panel has high-contrast styling, a spinner while the action runs, disables stack action buttons (e.g. **Starting…**), and a **Close** button that clears the log when idle.

- **Dev stack create (Magento):** Fix `NameError` in `stack_access_info` for `magento-min` / `magento-full` presets; create API returns JSON errors instead of HTML 500 pages.

- **Dev stack presets (audit):** Hardened all ready stacks (WordPress, WooCommerce, Joomla, Magento, Drupal, Ghost, Elasticsearch) — richer access metadata, WooCommerce `wc-setup` waits for WordPress install, JSON error responses on dev-stack APIs, and regression tests for every template preset.

- **Magento dev stacks:** `magento-min` / `magento-full` now use `bitnamilegacy/magento-archived:2` (Bitnami removed `docker.io/bitnami/magento` from Docker Hub). Existing stacks: edit `docker-compose.yml` or destroy/recreate from the Platform tab.

- **Magento full (Varnish/Nginx):** Edge config uses Compose `configs` instead of host bind mounts so **Start** works when the dashboard runs under `/project` (no Docker Desktop file-sharing for `varnish/default.vcl`). Existing `magento-full` stacks auto-upgrade on **Start**.

- **Magento URL repair:** Waits for Bitnami first-boot (`/bitnami/magento/bin/magento`, `setup:db:status`) before `bin/magento` URL repair; skips with a clear message instead of exec errors. Nginx edge config uses `$$` in Compose so `proxy_set_header` variables are not stripped.

- **Magento dev stacks (MariaDB):** Use `bitnamilegacy/mariadb:10.6` (Magento does not support MariaDB 11). Existing stacks: **Destroy** (with volumes) and create/start again, or `compose down -v` then **Start** after the compose file is upgraded.

- **Dev stack configuration UI:** Each stack card has **Advanced — configuration & files** (collapsed by default) with paths (`platform/dev-stacks/<id>/`, `hosting/traefik/20-dev-stacks.yml`, `config/leco-platform.yaml`) and in-browser edit/view for stack files via `/api/dev-stacks/<id>/config` and `/files`.

- **Dev stack Repair / Reinstall:** **Repair** applies LEco configuration updates (images, edge configs), Traefik routes, `lh-network` connectivity, `compose up -d`, and public URL repair — keeps volumes and manual Advanced edits. **Reinstall** regenerates stack files from the template (reverts edits), wipes volumes, and fully redeploys/reconfigures (fixes bad DB state, e.g. Magento on MariaDB 11). API action `redeploy` remains an alias for `reinstall`.

- **Dev stack cards (Platform):** Full-width two-column layout per stack with **Networking** flow diagram, **Admin & credentials** (open admin, copy magic link, reset for WordPress/Magento), **Quick open** (storefront, Adminer, Redis Commander), and **Data stores** (Docker connection strings + CLI hints).

- **Dev stack frameworks:** New **Application frameworks** preset group — Yii2, CakePHP, Symfony, Laravel, Django, Ruby on Rails, NestJS, FastAPI, Flask, and Express. Each stack bootstraps on first Start (composer/npm/pip), exposes the app on `{stackId}.lh` via Traefik, and includes DB services where applicable.

- **Dev stack image preflight:** Central image registry (`dev_stack_images.py`), auto-rewrite of deprecated Bitnami Magento/MariaDB refs on start, registry checks before `compose up`, and create-time validation so API errors stay JSON (not HTML).

- **Traefik 404 on `localhost.lh`:** Empty `hosting/traefik/20-dev-stacks.yml` no longer writes invalid `http.routers: {}` (Traefik v3 rejected the whole file provider). `traefik.sh heal` normalizes every `hosting/traefik/*.yml`.

- **Control — service dependencies:** Stopping **n8n** now stops **n8n_postgres** automatically; stopping Postgres stops n8n first. Infra targets cascade similarly (e.g. **cache-varnish** / **redis-commander** with their backends).

### Added

- **SRS — Cloud VM platform:** [`docs/SRS_CLOUD_VM_PLATFORM.md`](docs/SRS_CLOUD_VM_PLATFORM.md) — requirements for cloud VM install profiles, dev stacks, custom domain/TLS, and dashboard platform operations.
- **Cloud VM platform:** Install profiles (`cloudflare-full`, `ai-full`, `ai-cloud`, …), `config/leco-platform.yaml`, `ecosystem-stack/lib/platform_config.py`, cloud installer, Platform dashboard tab, `/api/platform/*` and `/api/dev-stacks/*`, isolated dev stack compose generator, Traefik domain render + ACME TLS mode, `platform.devStackId` schema and hosting overlay, docs [`CLOUD_VM_DEPLOYMENT.md`](docs/CLOUD_VM_DEPLOYMENT.md) and [`DEV_STACK_ISOLATION.md`](docs/DEV_STACK_ISOLATION.md).
- **Cloud VM — ai-cloud:** Installer seeds `config/ai-providers.yaml` with external provider default; Infrastructure AI panel shows cloud-first banner when `ai-cloud` / `prefer_cloud` is active.
- **Cloud VM — dev stack binding:** Hosted apps **Dev stack** dropdown saves `platform.devStackId`; register/deploy apply `docker-compose.leco-devstack.yml`; cloud mode rewrites `*.lh` URLs in manifest UI, attached services, and registration summaries.
- **Dev stack builder — presets & ready apps:** `ecosystem-stack/config/dev-stack-presets.yaml` with infrastructure levels (1–6), common bundles (LAMP, MEAN, data stores, …), and ready stacks (WordPress, WooCommerce, Joomla, Magento Open Source minimum/full, standalone Elasticsearch, Drupal, Ghost) with optional **sample / demo content**; templates generate multi-service compose in `dashboard/dev_stack_templates.py`.

- **UI credential vault (local dev):** gitignored `config/ui-credentials.yaml`, registry JSON, Infrastructure **UI access** panel, hub actions, login-assist routes, and reset/apply for MinIO, MySQL, and PostgreSQL.
- **CF ↔ LEco service map:** `docs/CF_LECO_SERVICE_MAP.md`, `ecosystem-stack/config/cf-leco-service-registry.json`, Docs catalog entry, and cross-links across help/samples.
- **Control default policies:** per-target `start` / `stop` / `offloaded` with API, example JSON, and Infrastructure **Start stacks** shortcuts.
- **Infrastructure:** Cloudflare-local stack container table, Valkey probe, and `update-catalog` in ecosystem start order.
- **Hosted apps — attached services:** Per-app panel lists compose containers, edge runtimes, Cloudflare KV/R2/D1 bindings, and ecosystem data stores with inline credentials, connection strings, and management UI links (`attached_services` on snapshot API).
- **Attached services (Mongo/Redis):** Reads compose via `compose_tail` fallback, `.env` beside compose, and `MONGODB_URI` / `REDIS_URL` from app services; builds default `mongodb://` / host-port connection strings and mongo-express links when present.
- **Docs — attached services:** User help (`docs/help/12-hosted-app-attached-services.md`), developer API reference (`docs/help/dev-08-hosted-app-services.md`), and cross-links in LLD, playbook, DEPLOY_CLI, and user manual.
- **Hosted apps — seed data import:** `hosting/app-available/<slug>/data/` convention, `leco-devops import-data`, dashboard **Seed data** card with **Import data** / dry-run NDJSON stream, importers for MongoDB, MySQL, Postgres, Redis, D1, R2, KV, and files. Operator guide `docs/help/13-hosted-app-data-import.md`, developer reference `docs/help/dev-09-data-import.md`, scaffold `data/` template on `leco-devops scaffold`.

### Changed

- **Wrangler local bindings:** browser/hyperdrive/email documented as partial bridges; narrowed `productionOnlyBindings` defaults in Workers adapter.
- **MinIO:** `MINIO_BROWSER_REDIRECT_URL` so `s3.lh` browsers redirect to `minio-console.lh` instead of `:9001`.

### Fixed

- **Infrastructure health:** Intentionally stopped containers (exited/paused) and services with **stop** or **offloaded** policy no longer fail HTTP probes or mark the platform **degraded**; probes show **n/a** instead of 502.
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

[Unreleased]: https://github.com/leco-devops/local-ecosystem/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/leco-devops/local-ecosystem/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/leco-devops/local-ecosystem/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/leco-devops/local-ecosystem/releases/tag/v0.1.0
