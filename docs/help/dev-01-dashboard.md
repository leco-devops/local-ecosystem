# Dashboard architecture

Flask app in `dashboard/`. Entry: **`app.py`** (~100+ routes). Static: `static/dashboard.js`, templates in `templates/`.

## Core modules

| Module | Role |
|--------|------|
| `app.py` | HTTP routes, wires modules together |
| `monitor.py` | `collect_overview()`, service health, `SERVICE_MAP`, CF-local status |
| `control.py` | `POST /api/control` — stack, CF, infra, `leco-stack-*` actions |
| `control_targets.py` | Controllable units for UI |
| `leco_control.py` | Registry → compose metadata, `leco-stack-<id>` targets |
| `hosted_apps.py` | `/api/hosted-apps*`, snapshots, logs, pending registration |
| `hosted_app_services.py` | Attached services payload (`connection_endpoints`, credentials, compose merge) |
| `hosted_offboard.py` | Teardown orchestration after down/unregister |
| `hosted_zip_upload.py` | Zip → `app-available/<slug>` |

## LEco registration stack

| Module | Role |
|--------|------|
| `leco_detect.py` | Path resolution (`wsp:`), scan, YAML defaults, overlays |
| `leco_materialize.py` | `materialize_registration_yaml`, `save_registration_yaml` |
| `hosting_layout.py` | `hosting/app-available/<slug>/`, `source` symlink, configRefs symlinks |
| `leco_registration.py` | `register_app_wizard`, `prepare_register_from_disk` |
| `leco_subprocess.py` | `run_ecosystem_register`, `run_leco_deploy`, streaming iterators |
| `leco_validate.py` | Pydantic via `leco_app.schema` |

## Traefik (dashboard side)

| Module | Role |
|--------|------|
| `traefik_dynamic_file.py` | Read/merge `hosting/traefik/dynamic.yml` for Routes tab |
| `traefik_manifest_keys.py` | Router/service key naming |

CLI owns fragment **generation**; dashboard may call same merge helpers for API consistency.

## AI onboarding

| Module | Role |
|--------|------|
| `ai_config.py` | `config/ai-providers.yaml` |
| `ai_provider.py` | Ollama, AirLLM, OpenAI, Anthropic, Google, hybrid |
| `ai_orchestrator.py` | `run_onboarding`, `stream_onboarding`, `write_generated_files` |
| `ai_file_collector.py`, `ai_prompts.py`, `ai_template_generator.py` | Pipeline |

Routes: `/api/leco/ai-analyze/stream`, `/api/leco/ai-analyze/write`.

## Runtimes

`dashboard/leco_runtimes/` — `base.py`, `cloudflare_workers.py` (implemented); other types stubbed.

`ensure_local_runtime_overlay()` in `leco_detect.py` writes `docker-compose.leco-runtime.yml`.

## Help & docs in UI

| Module | Role |
|--------|------|
| `docs_catalog.py` | Docs tab catalog → `/api/docs/content` |
| `help_manual.py` | Help tree + search → `/api/help/*` |

## Key LEco API routes

| Method | Path | Module |
|--------|------|--------|
| GET | `/api/leco/browse` | `leco_detect` |
| POST | `/api/leco/detect` | `leco_detect` |
| POST | `/api/leco/generate-yaml` | `leco_materialize` |
| POST | `/api/leco/save-yaml` | `leco_materialize` |
| POST | `/api/leco/register` | `leco_registration` |
| POST | `/api/leco/register/stream` | streaming register logs |
| POST | `/api/hosted/upload-zip` | `hosted_zip_upload` |
| POST | `/api/hosted-apps/<slug>/offboard` | `hosted_offboard` |
| POST | `/api/control` | `control` |

Grep `@app.` in `app.py` for the full list.

## Container mounts (local dev)

`ecosystem-stack/services/dashboard.sh` mounts:

- `$PROJECT_ROOT:/project:rw` — repo root (`DASHBOARD_PROJECT_ROOT`)
- Workspace parent read-only for `wsp:` paths
- `DASHBOARD_CONTROL_TOKEN`, `DASHBOARD_*_HOST` for path remapping on Docker Desktop

After Python/template/static edits: **`restart dashboard`** (bind mount) or **`dashboard.sh deploy`** (image rebuild).

## Sync rule with CLI

Dashboard **must not** duplicate register/merge logic — call **`leco-devops`** via `leco_subprocess.py`. Pre-register Python steps (`ensure_lh_network_hosting_overlay`, `ensure_local_runtime_overlay`) prepare disk so CLI reads consistent YAML.

When changing manifest merge or compose resolution, update **`schema.py`** and both **`leco_detect.py`** and **`compose_runner.py`**.

Next: [CLI & schema](help:dev-cli) · [Attached services API](help:dev-hosted-app-services)
