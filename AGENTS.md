# AGENTS.md - LEco DevOps Open Project agent guide

This file gives automation agents the minimum complete context to work safely and effectively in this repository.

## Project identity

- Project: **LEco DevOps Open Project**
- Application: **LEco DevOps** (web UI + LEco tooling experience)
- License: **MIT** (`LICENSE`)
- Maintainer: **Rajneesh Maurya**

## Repository intent

This repo is a local platform with:

- Traefik edge routing on `*.lh`
- Ecosystem stack services (Ollama, AirLLM, Open WebUI, n8n, Postgres)
- LEco DevOps dashboard and APIs
- LEco app toolchain (`leco-devops`; Python package name remains `leco-app` on PyPI)
- Optional Cloudflare-local and infra compose stacks

## Critical paths

- Orchestration: `ecosystem-stack/ecosystem-stack.sh`, `ecosystem-stack/core.sh`, `ecosystem-stack/services/*.sh`
- Dashboard: `dashboard/`
- LEco CLI: `tools/deploy-cli/leco_app/`
- Hosting layout: `hosting/app-available/` (optional **`docker-compose.leco-hosting.yml`** + **`additionalComposeFilesFromManifest`** for LEco-only compose merges beside `leco.app.yaml`); reference YAML packs: `hosting/samples/` (not scanned as staging apps)
- Registry: `config/leco-registry.yaml`
- UI credential vault (local dev): `ecosystem-stack/config/ui-login-registry.json`, `config/ui-credentials.yaml` (gitignored), `dashboard/ui_credentials.py`, `dashboard/ui_credential_reset.py` — see `docs/UI_CREDENTIAL_VAULT.md`
- Traefik routes: canonical `traefik/dynamic.yml`; runtime file provider dir `hosting/traefik/` (`01-stack-core.yml` = copy on each Traefik start; `dynamic.yml` = merge target for `leco-devops` / dashboard). Use `ecosystem-stack/services/traefik.sh` **`heal`** / **`ensure-hosting-files`** when fixing global 404 or invalid empty `http` YAML.
- Primary docs: `README.md`, `docs/`

## Architecture docs (read first for large changes)

- `docs/ARCHITECTURE.md`
- `docs/HLD.md`
- `docs/LLD.md`
- `docs/LECO_TOOLING.md`
- `docs/LECO_APP_BLUEPRINT.md`
- `docs/DEVELOPMENT_PLAYBOOK.md`
- `docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md` (Traefik + Hosted apps: 502, `lh-network`, probes, routing normalization)

## Agent operating rules

1. Do not break naming conventions:
   - Application/UI/CLI product name: `LEco DevOps`
   - Project/repository brand: `LEco DevOps Open Project`
2. Keep the published CLI entrypoint name **`leco-devops`** unchanged unless explicitly requested (Python import path remains `leco_app`).
3. Prefer additive edits over broad rewrites in docs-heavy areas.
4. When changing hosted app behavior, review and keep these modules aligned:
   - `dashboard/leco_detect.py`
   - `dashboard/leco_materialize.py`
   - `dashboard/hosting_layout.py`
   - `dashboard/leco_registration.py`
   - `tools/deploy-cli/leco_app/schema.py`
   - When changing attached services / per-app connection strings, also update:
     - `dashboard/hosted_app_services.py`
     - `dashboard/hosted_apps.py` (snapshot)
     - `dashboard/static/dashboard.js` (Attached services UI)
     - `docs/help/12-hosted-app-attached-services.md`, `docs/help/dev-08-hosted-app-services.md`
   - When changing update-catalog / LLM catalogs, also update:
 - `ecosystem-stack/update-catalog/watcher.py`
 - `ecosystem-stack/config/llm-catalog-*-seed.json`
 - `ecosystem-stack/services/update-catalog.sh`
 - `dashboard/ecosystem_updates.py`
 - When changing AirLLM behavior, also update:
     - `dashboard/airllm_models.py`
     - `dashboard/ai_provider.py` (`AirLLMProvider`)
     - `ecosystem-stack/services/airllm.sh`
     - `ecosystem-stack/airllm/Dockerfile` + `requirements.txt` + `server.py`
     - `traefik/dynamic.yml` `airllm-service` (points at `http://airllm:11435`)
     - `docs/AIRLLM_INTEGRATION.md`
   - When changing Cloudflare-local or binding behavior, also update:
     - `docs/CF_LECO_SERVICE_MAP.md`
     - `ecosystem-stack/config/cf-leco-service-registry.json`
     - `dashboard/control_targets.py` (CF_TARGETS)
     - `dashboard/monitor.py` (SERVICE_MAP, CLOUDFLARE_ENDPOINTS, collect_cloudflare_local_status)
5. When changing routing semantics, update all of:
   - `traefik/dynamic.yml` and merge target `hosting/traefik/dynamic.yml` behavior/docs
   - CLI route generation/merge code
   - related docs (`docs/DEPLOY_CLI.md`, `docs/LECO_APP_BLUEPRINT.md`)
6. Keep in-app docs discoverable:
   - Add new docs to `dashboard/docs_catalog.py` if they should appear in UI Docs tab.
7. Keep destructive operations explicitly documented and token-gated where applicable.

## Validation checklist for agents

- Python syntax: `python3 -m compileall -q dashboard tools/deploy-cli/leco_app`
- Check for regressions in docs links from `README.md` and `dashboard/docs_catalog.py`
- If adding docs, ensure paths are repo-root relative and loadable via `/api/docs/content`
- User-visible changes: add `[Unreleased]` bullet in `CHANGELOG.md`; see `docs/VERSIONING.md` for releases
- Hosted apps / Traefik behavior: see `docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md` when changing `dashboard/leco_detect.py`, `dashboard/monitor.py`, or hosting overlays

## High-value follow-up conventions

- Use `README.md` Documentation + Important links sections for top-level discoverability.
- Use `docs/DEVELOPMENT_PLAYBOOK.md` as developer index for architecture and component references.
- Keep OSS docs (`LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`) current and linked.
