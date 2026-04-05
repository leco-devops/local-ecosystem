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
- Ecosystem stack services (Ollama, Open WebUI, n8n, Postgres)
- LEco DevOps dashboard and APIs
- LEco app toolchain (`leco-app` / `leco-devops`)
- Optional Cloudflare-local and infra compose stacks

## Critical paths

- Orchestration: `ecosystem-stack/ecosystem-stack.sh`, `ecosystem-stack/core.sh`, `ecosystem-stack/services/*.sh`
- Dashboard: `dashboard/`
- LEco CLI: `tools/deploy-cli/leco_app/`
- Hosting layout: `hosting/app-available/`
- Registry: `config/leco-registry.yaml`
- Traefik routes: `traefik/dynamic.yml`
- Primary docs: `README.md`, `docs/`

## Architecture docs (read first for large changes)

- `docs/ARCHITECTURE.md`
- `docs/HLD.md`
- `docs/LLD.md`
- `docs/LECO_TOOLING.md`
- `docs/LECO_APP_BLUEPRINT.md`
- `docs/DEVELOPMENT_PLAYBOOK.md`

## Agent operating rules

1. Do not break naming conventions:
   - Application/UI/CLI product name: `LEco DevOps`
   - Project/repository brand: `LEco DevOps Open Project`
2. Keep CLI command names unchanged (`leco-app`, `leco-devops`) unless explicitly requested.
3. Prefer additive edits over broad rewrites in docs-heavy areas.
4. When changing hosted app behavior, review and keep these modules aligned:
   - `dashboard/leco_detect.py`
   - `dashboard/leco_materialize.py`
   - `dashboard/hosting_layout.py`
   - `dashboard/leco_registration.py`
   - `tools/deploy-cli/leco_app/schema.py`
5. When changing routing semantics, update all of:
   - `traefik/dynamic.yml` behavior/docs
   - CLI route generation/merge code
   - related docs (`docs/DEPLOY_CLI.md`, `docs/LECO_APP_BLUEPRINT.md`)
6. Keep in-app docs discoverable:
   - Add new docs to `dashboard/docs_catalog.py` if they should appear in UI Docs tab.
7. Keep destructive operations explicitly documented and token-gated where applicable.

## Validation checklist for agents

- Python syntax: `python3 -m compileall -q dashboard tools/deploy-cli/leco_app`
- Check for regressions in docs links from `README.md` and `dashboard/docs_catalog.py`
- If adding docs, ensure paths are repo-root relative and loadable via `/api/docs/content`

## High-value follow-up conventions

- Use `README.md` Documentation + Important links sections for top-level discoverability.
- Use `docs/DEVELOPMENT_PLAYBOOK.md` as developer index for architecture and component references.
- Keep OSS docs (`LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`) current and linked.
