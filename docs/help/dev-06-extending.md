# Extending LEco

Extension points for new features without breaking dashboard ↔ CLI contract.

## New hosted-app manifest capability

1. **`tools/deploy-cli/leco_app/schema.py`** — Pydantic models + `load_effective_manifest`
2. **`dashboard/leco_validate.py`** — validation API
3. **`dashboard/leco_detect.py`** — scan defaults, `ensure_*_overlay` generators
4. **`tools/deploy-cli/leco_app/compose_runner.py`** — if compose `-f` logic changes
5. **`tools/deploy-cli/leco_app/traefik_fragment.py`** — if routing shape changes
6. **`dashboard/leco_registration.py`** — pre-register hooks
7. **`docs/LECO_APP_BLUEPRINT.md`** + help topic if user-facing

## Attached services / connection endpoints

1. **`dashboard/hosted_app_services.py`** — classification, `_build_connection_endpoints`, enrich, `build_attached_services`
2. **`dashboard/hosted_apps.py`** — snapshot wiring
3. **`dashboard/static/dashboard.js`** — `renderHostedAttachedServices()`
4. **`dashboard/tests/test_hosted_app_services.py`**
5. Help: **`docs/help/12-hosted-app-attached-services.md`** (users), **`docs/help/dev-08-hosted-app-services.md`** (developers)

Keep compose file resolution aligned with **`leco_detect`** / **`compose_runner`** when changing `load_merged_compose_services`.

## New local edge runtime type

1. `LocalRuntimeSpec` fields in **`schema.py`**
2. Adapter module **`dashboard/leco_runtimes/<type>.py`** implementing `RuntimeAdapter`
3. Image/recipe under **`infra/runtimes/<type>/`**
4. Register hook: **`ensure_local_runtime_overlay`** in `leco_detect.py`
5. Traefik upstream: **`traefik_fragment._upstream_routing_fragment`**
6. Sample under **`hosting/samples/`**

Currently implemented: **`cloudflare-workers`**. Stubs: pages, vercel, lambda, deno.

## New stack `*.lh` service

1. **`ecosystem-stack/services/<name>.sh`**
2. **`traefik/dynamic.yml`** routers/services
3. **`dashboard/monitor.py`** `SERVICE_MAP`
4. Optional **`dashboard/control_targets.py`** if controllable
5. **`ecosystem-stack/core.sh`** start order + network list

## New dashboard API

1. Route in **`dashboard/app.py`**
2. UI in **`static/dashboard.js`**
3. Control token gate if mutating (`control_token()` pattern)
4. Optional **`docs_catalog.py`** entry for Docs tab (technical)
5. Optional **`help_manual.py`** entry for Help (operator)

## New AI provider

1. **`dashboard/ai_provider.py`** — subclass `AIProvider`, register in `create_provider`
2. **`dashboard/ai_config.py`** — provider list / keys
3. **`config/ai-providers.yaml`** example
4. Infrastructure UI in `index.html` if settings needed

## New CLI command

1. Add Typer command in **`cli.py`**
2. Reuse **`compose_runner`**, **`onboarding`**, **`schema`** modules
3. Document in **`docs/DEPLOY_CLI.md`** and help **`06-cli.md`**

## Traefik / routing semantics change

Update **all** of (per `AGENTS.md`):

- `traefik/dynamic.yml`
- `tools/deploy-cli/leco_app/traefik_fragment.py`
- `tools/deploy-cli/leco_app/traefik_dynamic_merge.py`
- `dashboard/traefik_dynamic_file.py`
- `docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md`, `docs/DEPLOY_CLI.md`

## Naming conventions (do not break)

- Product UI: **LEco DevOps**
- Project: **LEco DevOps Open Project**
- CLI command: **`leco-devops`** (unchanged)

Next: [Debugging & validation](help:dev-debugging)
