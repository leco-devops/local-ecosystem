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
| `platform_config.py` | `deployment_mode`, `base_domain`, `tls_mode()`, `router_tls_config()`, `lh_to_public_host()` |
| `mcp_insights.py` | Reads `mcp-activity.jsonl` + probes `leco-mcp:8099/insights` → MCP tab payload |

## LEco registration stack

| Module | Role |
|--------|------|
| `leco_detect.py` | Path resolution (`wsp:`), scan, YAML defaults, overlays, `routing_domain()` / `app_hostname()`, mesh routing inference |
| `leco_wrangler_paths.py` | Wrangler `.toml` / `.json` / `.jsonc` discovery, JSONC stripping, Worker + Pages enumeration |
| `leco_materialize.py` | `materialize_registration_yaml`, `save_registration_yaml` |
| `hosting_layout.py` | `hosting/app-available/<slug>/`, `source` symlink, configRefs symlinks |
| `leco_registration.py` | `register_app_wizard`, `prepare_register_from_disk` |
| `leco_subprocess.py` | `run_ecosystem_register`, `run_leco_deploy`, streaming iterators |
| `leco_validate.py` | Pydantic via `leco_app.schema` |
| `git_source.py` | Git URL as an app source — validation, clone roots, credential isolation, streaming clone |

Detection rules worth knowing before you change them:

- Routing targets the **container** port, not the published host port — Traefik reaches apps over `lh-network`, where host mappings do not exist. `_parse_port_pairs()` keeps both sides.
- The walk *up* looking for a `wrangler.*` config stops at the first `.git` / `.hg` / `.svn` directory. Without that guard it adopts a sibling checkout's bindings.
- One compose service publishing several **distinct container ports** becomes one route per port: lowest port on `<slug>.<domain>`, the rest on labelled sub-hosts. Several host ports mapping to one container port is not a mesh.
- Hostnames come from `app_hostname()`, which resolves the suffix from `config/leco-platform.yaml`. Never hard-code `.lh`.

## Traefik (dashboard side)

| Module | Role |
|--------|------|
| `traefik_dynamic_file.py` | Read/merge `hosting/traefik/dynamic.yml` for Routes tab |
| `traefik_manifest_keys.py` | Router/service key naming |

CLI owns fragment **generation**; dashboard may call same merge helpers for API consistency.

## AI onboarding

| Module | Role |
|--------|------|
| `ai_config.py` | `config/ai-providers.yaml` (`0600`), key masking, provider presets, capability tiers |
| `ai_provider.py` | Ollama, AirLLM, OpenAI, Anthropic, Google, OpenAI-compatible, hybrid — plus model discovery |
| `ai_orchestrator.py` | `run_onboarding`, `stream_onboarding`, `write_generated_files` |
| `ai_file_collector.py`, `ai_prompts.py`, `ai_template_generator.py` | Pipeline |

Routes: `/api/leco/ai-analyze/stream`, `/api/leco/ai-analyze/write`, `/api/ai/settings`, `/api/ai/test`, `/api/ai/discover`, `/api/ai/models`.

`openai-compatible` is driven by a preset table in `ai_config.py` covering aggregators (Eden AI, OpenRouter, Groq, Together, DeepInfra, Fireworks, Cerebras, NVIDIA NIM), single vendors (DeepSeek, Mistral, xAI), a gateway (LiteLLM), local servers (vLLM, LM Studio, LocalAI, Ollama's OpenAI shim) and `custom`. An explicit `base_url` always wins over a preset. The provider screen lives on the **Service hubs** page (`/hub#hub-ai-providers`, template `hub_index.html`); the browser only ever receives masked keys and `*_set` booleans.

## RAG (ask the platform about itself)

| Module | Role |
|--------|------|
| `ai_corpus.py` | Index `docs/**`, a fixed root file list and the plugin skills; heading-aligned chunks; scrubbing; JSON index + optional vectors |
| `ai_rag.py` | BM25(F) retrieval, live-source rules and collectors, prompt assembly, streaming, retrieval-only fallback |

Routes: `/api/ai/rag/ask`, `/api/ai/rag/ask/stream`, `/api/ai/rag/reindex`, `/api/ai/rag/status`.

- Secrets are scrubbed **twice** — on every chunk at index time, and again on every live payload during prompt assembly. If you add a new secret-bearing file, add it to the exclusion list in `ai_corpus.py`.
- Live state is pulled at query time and only when a rule matches the question; a failing collector becomes a note in the context rather than a 500.
- Embeddings are optional and local (an Ollama embedding model). Retrieval falls back to lexical BM25 on any embedding failure.
- With no provider configured the endpoint still answers — it returns the retrieved passages, tagged `answered_by: "retrieval-only"`.

## Git source & CI/CD

| Module | Role |
|--------|------|
| `git_source.py` | Clone roots, `config/git-credentials.yaml`, `GIT_ASKPASS` / `GIT_SSH_COMMAND` temp-dir isolation |
| `cicd.py` | `config/cicd-pipelines.yaml`, signature verification, coalescing, pull → build → deploy → verify → record |

Two invariants that look like bugs if you do not know them:

- `POST /api/cicd/webhook/<pipeline_id>` intentionally **skips** `check_control_token`. Its authentication is the per-pipeline HMAC, verified with `hmac.compare_digest` before the body is parsed. Adding the token check would break every real webhook.
- On verify failure the pipeline's `last_deployed_sha` is **not** advanced — `step_verify` raises, so the record step never runs. The containers are already replaced; nothing rolls back automatically.

Run history: `ecosystem-stack/config/generated/cicd-runs.jsonl` (append-only, last snapshot per `run_id` wins).

## MCP

The MCP server (`tools/mcp-server/leco_mcp/`) is a **client** of this app, not part of it — it holds no Docker access and reimplements nothing. The dashboard side is read-only:

| Module | Role |
|--------|------|
| `mcp_insights.py` | Tail `ecosystem-stack/config/generated/mcp-activity.jsonl`, probe `leco-mcp:8099/insights`, join events onto the hosted-app fleet |

Routes: `GET /api/mcp/insights`, `GET /api/mcp/activity`, `GET /api/mcp/install`. UI: the **MCP** tab (`mcpTab`). The dashboard never writes the activity log — the MCP server does.

Details: [MCP server developer notes](help:dev-mcp-server) · `docs/MCP_SERVER.md`.

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
| GET | `/api/leco/git/config` | `git_source` |
| POST | `/api/leco/git/inspect` | `git_source` |
| POST | `/api/leco/git/clone/stream` | `git_source` (NDJSON) |
| GET | `/api/cicd/overview` | `cicd` |
| POST | `/api/cicd/pipelines` | `cicd` (secret returned once) |
| POST | `/api/cicd/webhook/<pipeline_id>` | `cicd` (HMAC, no control token) |
| GET | `/api/mcp/insights` | `mcp_insights` |
| POST | `/api/ai/rag/ask/stream` | `ai_rag` (NDJSON) |

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

Next: [CLI & schema](help:dev-cli) · [Attached services API](help:dev-hosted-app-services) · [Architecture & diagrams](help:architecture-diagrams)
