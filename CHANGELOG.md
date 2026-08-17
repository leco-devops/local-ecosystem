# Changelog

All notable changes to the **LEco DevOps Open Project** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Onboard from a Git repository:** the Register wizard takes a repo URL as well as a local folder — clone or update, pick a branch/tag/commit, private repos via HTTPS token or SSH key. The resolved path drops into the existing Detect → Generate → Register flow unchanged. Credentials reach `git` through `GIT_ASKPASS` / `GIT_SSH_COMMAND` reading `0600` files, never argv or the URL, so nothing lands in the clone's `.git/config`; they persist to the gitignored `config/git-credentials.yaml` and are returned masked. `git` and `openssh-client` added to the dashboard image ([`dashboard/git_source.py`](dashboard/git_source.py), `/api/leco/git/*`).
- **CI/CD:** signed GitHub / GitLab webhooks trigger pull → build hook → deploy → **verify** → record, per registered app. Verify is a real HTTP probe, so a deploy that leaves the app returning 502 is recorded as failed. Run history with per-step status and captured log, rollback to the previously deployed commit, branch filtering, and one run per pipeline at a time. Webhook secrets are generated, shown once, and stored gitignored ([`dashboard/cicd.py`](dashboard/cicd.py), `/api/cicd/*`).
- **Real-domain deployments:** hostname generation is no longer hardcoded to `.lh` — `dashboard/leco_detect.py` now resolves `deployment_mode` / `base_domain` for every generated URL, routing entry and mesh subdomain, with local `.lh` output proven byte-identical. `certs/generate-certs.sh` refuses to issue a meaningless mkcert bundle when `tls.mode` is `acme` / `cloudflare` / `static`, the Traefik renderer writes `tls.certResolver` onto TLS routers (no router referenced the ACME resolver before, so it could never have issued a certificate), and `cloud-install.sh` preflights instead of guessing.
- **Production hardening guide:** [`docs/PRODUCTION_HARDENING.md`](docs/PRODUCTION_HARDENING.md) — an audit of what the laptop-oriented defaults mean on a public host, with fixes and a pre-flight checklist. Findings include an unauthenticated Control API when `DASHBOARD_CONTROL_TOKEN` is unset (the dashboard mounts the Docker socket read-write, so that is host-root equivalent), Traefik's API running `insecure: true` and published on 8080, admin ports (8090, 8099, 8080, 5432) binding `0.0.0.0`, and `cloud-install.sh` performing no hardening at all.
- **External AI providers with model discovery:** Configuration screen on **Service hubs → AI providers (LLM access)** — named presets for Eden AI, Anthropic (Claude), Google (Gemini), OpenAI, OpenRouter, Groq and self-hosted gateways alongside local Ollama/AirLLM. Connecting lists the provider's real models (Eden AI returns ~897, OpenRouter ~414), searchable and tagged by capability tier, context window and cost where the provider reports them. Settings persist only to the gitignored `config/ai-providers.yaml`; API keys are never returned to the browser unmasked.
- **Ask LEco (RAG):** `POST /api/ai/rag/ask` (+ `/ask/stream`, `/reindex`, `GET /status`) answers questions about this platform and this machine, grounded in the repository's own documentation (112 files → 918 heading-aligned chunks, BM25) plus live state — stack status, app snapshots, Traefik routes and logs — pulled only when the question calls for it. Answers cite their sources, the response names which provider the text was sent to, and with no provider configured it still returns the relevant passages. Secrets are scrubbed at index time and again from live state, with tests that plant a key and assert it never reaches a prompt. Local embeddings via Ollama are an opt-in upgrade, never a dependency.
- **Complex application onboarding:** LEco now detects infrastructure in monorepos that keep it outside the repo root. Compose discovery searches `infra/docker/`, `infra/`, `deploy/`, `ops/`, `.docker/` and more (previously repo root and `docker/` only); Cloudflare Worker discovery accepts `wrangler.json` / `wrangler.jsonc` alongside TOML and enumerates every worker in a workspace with a unique `runtime_id`; published ports are read from the compose service that owns them; and a container publishing many ports now generates one Traefik route per port (`<runtime>.<slug>.lh`, lowest port as the front door). Verified against an 18-worker pnpm monorepo that previously produced a manifest with no `dockerCompose`, no `runtimes` and no `routing`.
- **Evidence-driven onboarding over MCP:** five tools (65 total) so an agent can onboard an application whose infrastructure convention-based detection cannot see. `leco_app_evidence` reports which compose service owns which port, the container name, container-vs-published port pairs and every Worker — each attributed port carrying an `owner_source` naming the file the number came from, and everything undetermined listed under `unknowns` instead of guessed. `leco_compose_validate` merges the app's compose with a proposed overlay and reports what Docker actually resolves, catching `ports: !reset` (clears the list; `!override` is what replaces it). `leco_manifest_overlay` backs up rather than clobbers. `leco_verify` probes every declared URL and classifies each as `ok` / `route_missing` / `backend_unreachable` / `tls_invalid` / `unhealthy` with the resolved Traefik router attached, because those need different fixes and a bare 502 names none of them. `leco_certs_refresh` reissues the local certificate after hostnames change. Endpoints `GET /api/leco/evidence`, `POST /api/leco/compose/validate`, `POST /api/leco/verify` ([`dashboard/app_evidence.py`](dashboard/app_evidence.py)); guide [`docs/ONBOARDING_COMPLEX_APPS.md`](docs/ONBOARDING_COMPLEX_APPS.md). Verified end to end against an 18-worker Cloudflare Workers mesh: four origins onboarded, deployed through LEco and all four verified `ok` over valid TLS.
- **MCP dashboard:** New **MCP** tab — server status and safety gates, install commands, a full-width **Plugin commands** table read from the plugin on disk, connected agents, per-application MCP activity, the audited activity log, and tool usage. Sessions, activity and tool tables have filters and pagination. Backed by `/api/mcp/insights`, `/api/mcp/activity` and `/api/mcp/install` ([`dashboard/mcp_insights.py`](dashboard/mcp_insights.py)).
- **MCP activity telemetry:** Every tool call and session boundary is appended to `ecosystem-stack/config/generated/mcp-activity.jsonl` with argument values passed through a scalar name whitelist, so the dashboard can answer "which agent touched which application, and when" without any route for credentials to reach the log. The HTTP transport also serves the same data live at `GET /insights`.
- **MCP server:** Model Context Protocol access to LEco DevOps for AI agents — [`tools/mcp-server/`](tools/mcp-server/) (`leco-mcp`), 65 tools across observability, Control lifecycle, hosted-app onboarding and deployment, Platform and dev stacks, Traefik routing, LLM models, and in-app docs; stdio transport for local Claude Code plus streamable HTTP at `https://mcp.lh/mcp`; stack service [`ecosystem-stack/services/mcp.sh`](ecosystem-stack/services/mcp.sh) (`leco-mcp` container, Control target `ai-mcp`, install profiles `agent-full` and `full`); guide [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md), Help [`docs/help/20-mcp-server.md`](docs/help/20-mcp-server.md) and [`docs/help/dev-11-mcp-server.md`](docs/help/dev-11-mcp-server.md).
- **MCP safety gates:** Destructive tools (`remove`, `reset`, `destroy`, `reinstall`, offboard, route strip, model delete, credential reset) require **both** `confirm=true` on the call and `LECO_MCP_ALLOW_DESTRUCTIVE=1` on the server; credential tools are separately gated by `LECO_MCP_ALLOW_CREDENTIALS=1`. Blocked calls report both gates so an agent surfaces the refusal instead of working around it.
- **Agent route map:** Repo-root [`START_HERE.md`](START_HERE.md) — install, deploy an application, operate the stack, drive it over MCP, and install the Skill and plugin; linked from [`AGENTS.md`](AGENTS.md) and the dashboard **Docs** tab.
- **Claude Code plugin:** Installable **LEco DevOps** plugin under [`tools/claude-plugin/`](tools/claude-plugin/) bundling the `leco-devops` MCP server, a progressive-disclosure operations skill (`skills/operate/SKILL.md` + five reference files), slash commands `/leco:status`, `/leco:up`, `/leco:diagnose`, `/leco:onboard`, `/leco:routes`, and a read-only `leco-diagnostician` subagent; marketplace entry [`.claude-plugin/marketplace.json`](.claude-plugin/marketplace.json) (`claude plugin marketplace add ./`).
- **MCP project scope:** Repo-root [`.mcp.json`](.mcp.json) registers the `leco-devops` MCP server for project-scoped Claude Code use; launcher [`tools/claude-plugin/bin/leco-mcp-launch`](tools/claude-plugin/bin/leco-mcp-launch) resolves `leco-mcp` from `LECO_MCP_BIN`, `PATH`, an importable `leco_mcp`, or the [`tools/mcp-server/`](tools/mcp-server/) source tree.
- **Docs:** Cloud VM install with Cloudflare-managed SSL — [`docs/CLOUDFLARE_SSL_INSTALL.md`](docs/CLOUDFLARE_SSL_INSTALL.md) and Help [`docs/help/19-cloudflare-ssl-install.md`](docs/help/19-cloudflare-ssl-install.md) (proxied wildcard DNS, `tls.mode: cloudflare`, Traefik route apply, Paperclip public URL).
- **Paperclip AI:** Agent orchestration stack service at `https://paperclip.lh` — `ecosystem-stack/services/paperclip.sh` + dedicated `paperclip_postgres`; Traefik routes; dashboard Infrastructure/Control cards; UI Access entry; install profiles `ai-full` and `full`; help guide [`docs/help/06-paperclip.md`](docs/help/06-paperclip.md).
- **Paperclip bootstrap CEO:** Interactive first-admin invite from dashboard (`Infrastructure → Paperclip`), `leco-cli.sh paperclip bootstrap-ceo`, `leco-devops platform paperclip-bootstrap-ceo`, and `ecosystem-stack.sh paperclip-bootstrap-ceo`.
- **File transfer stack:** FTP (`leco-ftp`, alpine-ftp-server) and SFTP (`leco-sftp`, atmoz/sftp) under [`file-transfer/docker-compose.yml`](file-transfer/docker-compose.yml); read-only web file browser at `files.lh` / `ftp-files.lh` / `sftp-files.lh`; dashboard **Control** and **Infrastructure** panels list SFTP, FTP, and browser cards under **Infra add-ons & file transfer**; ecosystem service script [`ecosystem-stack/services/file-transfer.sh`](ecosystem-stack/services/file-transfer.sh); install profile `file-transfer-full`; docs [`docs/FILE_TRANSFER.md`](docs/FILE_TRANSFER.md).
- **UI access (file transfer):** SFTP, FTP, and read-only file browser in Service hubs → UI access with copy-paste host/port/credentials and Edit / Reset & apply for protocol passwords via `file-transfer/.env`.
- **UI access ports:** Edit SFTP/FTP **Port** in UI access; writes `SFTP_PORT` / `FTP_PORT` to `file-transfer/.env` and recreates the container.
- **SFTP public-key auth:** Choose password, public key, or both in UI access Edit; dashboard writes `file-transfer/keys/sftp/<user>.pub` and `SFTP_AUTH_MODE`.
- **File transfer defaults:** SFTP/FTP default password is `leco#localhost-192` (was `leco`).
- **Docs:** FTP/SFTP architecture and operator/developer manuals (`FILE_TRANSFER.md`, Help `12-file-transfer`, `dev-10-file-transfer`; updates to `ARCHITECTURE.md`, `HLD.md`, `LLD.md`).

### Changed

- **Dashboard navigation grouped:** thirteen flat tabs wrapped onto a second row; related tabs are now dropdown groups — **Deploy** (Hosted apps, CI/CD, Routes), **Operate** (Control, Infrastructure), **Insight** (Metrics, Logs, Reference), **Platform** (Platform, MCP) — beside Overview, Help and Service hubs. Seven top-level items on one row; tab buttons keep their existing `data-tab` contract so switching behaviour is unchanged.
- **Plugin commands are namespaced:** The Claude Code plugin is now `leco` (was `leco-devops`) and its commands are skills, so they invoke as `/leco:status`, `/leco:onboard`, `/leco:diagnose` … instead of `/leco-status`. Nineteen skills cover status, onboarding, deploy, logs, routes, dev stacks, platform, models, docs, credentials and offboard. Install with `claude plugin install leco@leco-devops-open-project`; uninstall the old `leco-devops@…` identifier first or both appear in the command list.
- **GitHub Pages:** Remove duplicate logo from hero panel (brand stays in page header only).
- **Platform dev stacks:** Stack files list in Advanced panel uses a two-column grid (fills left-to-right for any file count).
- **UI:** Remove CSS and SVG gradients across dashboard, GitHub Pages theme, and brand logos; keep existing dark cyan/violet color scheme.
- **GitHub Pages:** Download .zip / .tar.gz header buttons open the repository on GitHub instead of triggering a direct archive download.
- **Update catalog:** Track [`ecosystem-stack/config/generated/update-catalog-read-state.json`](ecosystem-stack/config/generated/update-catalog-read-state.json) in git (removed from `.gitignore`).

### Fixed

- **One broken app no longer hides every healthy one.** An app whose `root:` symlink could not be resolved raised `OSError` out of `leco_meta_for_slug`, so `GET /api/hosted-apps` answered **500** and *every* application vanished from the dashboard though nothing was wrong with them. Unresolvable apps now degrade to a logged warning and are skipped individually ([`dashboard/leco_control.py`](dashboard/leco_control.py)). The trigger is worth knowing: a hosted app's `source` symlink must be written in **container** coordinates (`/workspace-parent/<Org>/<App>`) — a host-absolute path looks right on the host but resolves outside every mount the dashboard can see, and creating the symlink while the container runs leaves the bind mount stale (`stat()` answers `EINVAL`) until `docker restart service-dashboard`.

- **Secret backups were committable:** `.gitignore` named `config/ai-providers.yaml` by exact path, so a timestamped `config/ai-providers.yaml.bak-<stamp>` written beside it — containing a live API key, mode `0644` — matched no rule and was one `git add -A` from being committed. Added `config/*.bak-*`, `*.bak`, `*.orig`, `*~`, and tightened the existing backup's mode. Same class of gap as the certificate backups fixed earlier.
- **Help pages linked to files that do not exist:** eleven references were broken — `../../DOC.md` from `docs/help/` resolves to the repo root while the docs live in `docs/`, and `help:troubleshooting` names a folder node with no page. Relative links now point at the Docs reader by id; the folder reference points at `help:ts-common`.
- **The DNS/certificate guide described the old broken setup:** it called the certificates "self-signed" and told users to expect a browser warning. With `certs/generate-certs.sh` and a trusted mkcert CA there should be no warning; the page now documents the script, the re-run needed after onboarding a new hostname, a verification command that cannot produce a false pass, and why `mkcert "*.lh"` must not be used.
- **The control token never reached the dashboard container:** `DASHBOARD_CONTROL_TOKEN` appeared only as a comment in [`ecosystem-stack/services/dashboard.sh`](ecosystem-stack/services/dashboard.sh) and was never passed to `docker run`, so exporting it looked like it enabled authentication while `control.py` — which fails *open* on an empty token — kept accepting every caller. With the Docker socket mounted read-write and the Control API shelling out to the stack scripts, that is unauthenticated code execution on the host for anyone who can reach port 8090. The token is now forwarded when set, and the unset case behaves exactly as before.
- **`OllamaProvider` was un-instantiable:** its `analyze` / `health_check` / `list_models` were mis-indented into `AirLLMProvider`, leaving an abstract class — `GET /api/ai/models` returned HTTP 500 for any configuration touching Ollama, including the hybrid default.
- **Hybrid provider ignored its own model choice:** the sub-provider config already carried a `default_model`, so `setdefault` discarded the hybrid's `cloud_model` and calls went out with a stale model name (Anthropic `HTTP 404`). Explicit now wins over inherited.
- **Anthropic requests rejected by newer models:** `temperature` is refused outright by Claude 5 (`HTTP 400 temperature is deprecated for this model`) rather than ignored, so it is now sent only to generations that accept it. Curated fallback model names refreshed to what the API currently serves.
- **`wrangler.jsonc` crashed registration:** `parse_wrangler_cf_resources` was TOML-only and raised `TOMLDecodeError` *after* the registry entry had been written, leaving the app half-registered. It now parses TOML, JSON and JSONC (comment- and trailing-comma tolerant, string-literal safe).
- **Cross-project configuration leak (all apps):** Wrangler resolution walked up to six directory levels above the application with no repository boundary, so an app without its own config silently adopted the first `wrangler.toml` found in an unrelated sibling checkout — wiring another project's KV/R2/D1 bindings into the manifest. The walk now stops at a `.git` / `.hg` / `.svn` boundary.
- **Invented Worker ports:** A multi-worker repository was assigned a sequential `8787+` range that did not match its compose file, producing a plausible-looking manifest that routed every worker to the wrong port. Ports are now taken from the compose service that publishes them, and detect warns when the worker-to-port pairing cannot be inferred from the repository.
- **Compose services were invisible during preview:** `_load_compose_services_for_localhost` returned the detect loader's `None` (that loader requires a written `leco.app.yaml`) instead of falling through to its own resolution, so routing was never inferred for a not-yet-registered app.
- **Local HTTPS was never valid (`*.lh` wildcard certificate):** [`docs/SETUP.md`](docs/SETUP.md) instructed `mkcert "*.lh"`, but a wildcard directly below a top-level domain is rejected by every TLS client (RFC 6125 / CA-Browser Forum), so the certificate matched **no hostname at all** — not even `dashboard.lh`. The chain verified while the hostname check failed, which is why browsers kept showing *Not secure* after a successful `mkcert -install`. New generator [`certs/generate-certs.sh`](certs/generate-certs.sh) discovers every `*.lh` hostname from Traefik routers, the registry, and materialized apps, issues one certificate with explicit SANs (plus legal `*.<app>.lh` wildcards for multi-label hosts), backs up the previous pair, and verifies coverage with `openssl -checkhost` before finishing. Certificate filenames are unchanged, so no Traefik config or mount changes are required.
- **Cert backups were not gitignored:** `certs/*.bak-*` added to [`.gitignore`](.gitignore) — the `.bak-<timestamp>` suffix escaped the existing `certs/*.pem` rule.

- **Paperclip crash loop (EACCES on `.env`):** Bootstrap/onboard `docker exec` ran as root and left root-owned files under `/paperclip/instances`; server runs as `node` and could not read `.env`. Exec now uses `-u node`; start/bootstrap heal ownership on `/paperclip/instances` (alpine sidecar offline, `exec -u root` when running).
- **Traefik stack routes:** `traefik.sh ensure-hosting-files` always refreshes `hosting/traefik/01-stack-core.yml` from `traefik/dynamic.yml` so new platform hosts (e.g. `paperclip.lh`) appear after heal without a manual file delete.
- **File transfer Control tab:** Dashboard file-transfer compose commands use the host repo path (`DASHBOARD_PROJECT_ROOT_HOST`) so Docker Desktop can bind-mount `file-transfer/keys/sftp` (fixes “mounts denied” for `/project/file-transfer/keys/sftp`).

### Added

- **Brand assets:** LEco logo set under [`assets/brand/`](assets/brand/) (hexagon mark with center dot only—no monogram text, horizontal lockup, round avatar PNG/SVG); favicon and Open Graph image updated; logo in GitHub Pages header/hero/footer, README, and dashboard header.

### Fixed

- **GitHub Pages FAQ:** Render FAQ and lower page sections via layout includes (not inside `index.md` markdown); fix `<slug>`, `<id>`, `<your-app>` placeholders in `index.md` that broke HTML parsing; use `xml_escape` on FAQ fields.

### Added

- **SEO & GEO:** Meta keywords, canonical URLs, Open Graph/Twitter cards, JSON-LD (WebSite, Organization, SoftwareApplication, FAQPage), `jekyll-sitemap`, [`robots.txt`](robots.txt) with AI crawler rules, FAQ section from [`_data/faqs.yml`](_data/faqs.yml).
- **LLM discovery files:** [`llms.txt`](llms.txt), [`llms-full.txt`](llms-full.txt), [`ai.txt`](ai.txt) for generative-engine optimization; optional Google Analytics / GTM IDs in [`_config.yml`](_config.yml).
- **Sitemap:** Explicit [`sitemap.xml`](sitemap.xml) (homepage + `llms.txt`, `llms-full.txt`, `ai.txt`) from [`_data/sitemap.yml`](_data/sitemap.yml); linked in `robots.txt` and site footer.

### Fixed

- **GitHub Pages:** Use cases cards use HTML instead of markdown inside `<div>` blocks so headings and bold text render correctly under Jekyll.

### Added

- **GitHub Pages (Jekyll):** [index.md](index.md) landing with [_layouts/default.html](_layouts/default.html), control-panel dark theme ([assets/css/leco.css](assets/css/leco.css)), [_includes/leco-footer.html](_includes/leco-footer.html), Vision &amp; Mission section, and [CNAME](CNAME) for `leco-project.us`.

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
