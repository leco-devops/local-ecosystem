# Development playbook — Local Ecosystem

This guide helps you **extend**, **debug**, and **ship** changes across the ecosystem stack, Traefik, Cloudflare-local adapters, and LEco DevOps.

**First-time setup:** [SETUP.md](SETUP.md) · **Deploy / stop / troubleshoot:** [DEPLOYMENT.md](DEPLOYMENT.md) · **Project hub:** [../README.md](../README.md)

## Architecture documentation map

- System overview: [ARCHITECTURE.md](ARCHITECTURE.md)
- High-level design: [HLD.md](HLD.md)
- Low-level design: [LLD.md](LLD.md)
- LEco toolchain details: [LECO_TOOLING.md](LECO_TOOLING.md)
- Agent context and guardrails: [../AGENTS.md](../AGENTS.md)
- Cloudflare ↔ LEco service map: [CF_LECO_SERVICE_MAP.md](CF_LECO_SERVICE_MAP.md) — binding coverage, reuse rules, adapter roadmap

## Versioning and releases

- **Current version:** [`VERSION`](../VERSION) · [`version.json`](../version.json)
- **Changelog:** [CHANGELOG.md](../CHANGELOG.md)
- **Release index:** [RELEASE_NOTES.md](RELEASE_NOTES.md)
- **Policy:** [VERSIONING.md](VERSIONING.md) · per-release files in [`releases/`](../releases/)
- **Bump script:** `./tools/release/bump-version.sh X.Y.Z`
- **Dashboard API:** `GET /api/version`

## 1. Repository map (where things live)

| Area | Path | Purpose |
|------|------|---------|
| Ecosystem stack orchestration | `ecosystem-stack/core.sh`, `ecosystem-stack/ecosystem-stack.sh` | Start order, network repair, service scripts |
| Per-service Docker scripts | `ecosystem-stack/services/*.sh` | `start` / `stop` / `build` for each container |
| Traefik stack routes (git) | `traefik/dynamic.yml` | Canonical `*.lh` rules; copied to **`hosting/traefik/01-stack-core.yml`** on **`traefik.sh start`** (duplicate in `ecosystem-stack/config/dynamic.yml` if you use that tree) |
| Traefik runtime merge file | `hosting/traefik/dynamic.yml` | Writable fragment merged by **`leco-devops`** / dashboard Routes; empty stub **`{}`** only (Traefik v3 rejects **`http: {}`**) |
| Traefik hosting repair | `ecosystem-stack/services/traefik.sh` **`heal`** / **`ensure-hosting-files`** | Fixes copies + YAML stub; **`dashboard.sh`** runs **`heal`** after start unless **`DASHBOARD_SKIP_TRAEFIK_HEAL=1`** |
| Compose overlay (hosting-only) | `hosting/app-available/<slug>/docker-compose.leco-hosting.yml` + **`additionalComposeFilesFromManifest`** | Merge Traefik **`lh-network`** / public URL env without editing the upstream app repo (`tools/deploy-cli/leco_app/compose_runner.py`) |
| Hosted apps — issues & fixes | [HOSTED_APPS_TRAEFIK_RUNBOOK.md](HOSTED_APPS_TRAEFIK_RUNBOOK.md) | 502, **`lh-network`**, DNS names, dashboard **`*.lh`** probes, same-origin **`/api`** |
| Cloudflare local | `cloudflare-local/docker-compose.yml`, `cloudflare-local/adapters/*` | R2, KV, D1, Workers, autoscaler |
| LEco DevOps | `dashboard/` | Flask app, metrics, control API, UI |
| TLS | `certs/` | mkcert wildcard for `*.lh` |

## 2. Daily commands

```bash
# Full stack (interactive menu)
./ecosystem-stack/ecosystem-stack.sh menu

# One service
./ecosystem-stack/ecosystem-stack.sh restart dashboard
./ecosystem-stack/ecosystem-stack.sh logs cloudflare-local

# Cloudflare stack only (from repo root)
./ecosystem-stack/services/cloudflare-local.sh start
./ecosystem-stack/services/cloudflare-local.sh recreate r2-adapter
./ecosystem-stack/services/cloudflare-local.sh backup
```

## 3. After changing dashboard code

The dashboard runs in Docker. Rebuild the image:

```bash
./ecosystem-stack/ecosystem-stack.sh restart dashboard
# Manual image build (context must be repo root — includes tools/deploy-cli for LEco DevOps):
# docker build -t local/service-dashboard:latest -f dashboard/Dockerfile . && docker rm -f service-dashboard && …
```

The container should mount **`$PROJECT_ROOT:/project`** so Control actions and in-dashboard docs can read the repo. The run script uses **`--restart unless-stopped`** so the dashboard comes back when the Docker daemon restarts (unless the container was explicitly stopped).

The **Docs** tab includes a generated module **Service management commands** (`service-management`) built from `control_targets.py` (per-service CLI aligned with Control).

## 4. Adding a new `*.lh` service

1. **Container**: create or extend a `ecosystem-stack/services/<name>.sh` script (or add a compose service on `lh-network`).
2. **Traefik**: add `routers` + `services` in **`traefik/dynamic.yml`** pointing at `http://<container>:<port>`, then restart Traefik so **`hosting/traefik/01-stack-core.yml`** is refreshed. Per-app merges from manifests use **`hosting/traefik/dynamic.yml`**.
3. **DNS**: ensure `something.lh` resolves (dnsmasq / etc.) to the host running Traefik.
4. **Dashboard** (optional): add URLs to `monitor.py` `SERVICE_MAP` for probes and to the static URL catalog in `dashboard/reference_data.py` (or rely on SERVICE_MAP-driven encyclopedia).

## 5. Adding a Cloudflare-local adapter

1. New folder under `cloudflare-local/adapters/<name>/` with `Dockerfile` + app.
2. Register service in `cloudflare-local/docker-compose.yml` on `lh-network`.
3. Traefik route + `CLOUDFLARE_ENDPOINTS` / `collect_cloudflare_local_status()` in `dashboard/monitor.py` if you want health tiles.
4. `dashboard/control.py` `CF_TARGETS` if you want Control actions.
5. `ecosystem-stack/core.sh` `NETWORK_CONTAINERS` for network repair.

## 6. Workers runtime

- Source: `cloudflare-local/adapters/workers-runtime/worker.js` (Miniflare 2 service-worker style).
- Rebuild: `docker compose -f cloudflare-local/docker-compose.yml up -d --build workers-runtime`.

## 7. Dashboard APIs (for automation)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/overview` | GET | Services, containers, Docker overview, system status |
| `/api/cloudflare-local` | GET | CF adapter health + counts |
| `/api/metrics/history` | GET | Time series (also appends a sample) |
| `/api/control/targets` | GET | Controllable units |
| `/api/control` | POST | `{ "target_id", "action", "token"? }` — includes `stack-ecosystem-all` (`bulk_ecosystem` in `ecosystem-stack/core.sh`; bulk teardown skips dashboard + default platform `traefik`/`postgres`; see **DEPLOYMENT.md**) |
| `/api/reference` | GET | Full URL catalog + probe results |
| `/api/docs/catalog` | GET | Documentation modules |
| `/api/docs/content` | GET | `?id=<module>` Markdown body |
| `/api/ollama/models` | GET | Pinned vs installed Ollama models + status |
| `/api/ollama/models/action` | POST | `{ "action": "pull"|"pull_all"|"delete"|"unload", "model"? }` (+ token if required) |

The static UI (`dashboard/static/dashboard.js`) writes **`local_ecosystem_dashboard_overview_v1`**, **`local_ecosystem_dashboard_metrics_v1`**, and **`dashboard_active_tab`** to **localStorage** after successful fetches (and hydrates from them on load). Control results are stored under **`dashboard_last_control_result`**.

## 8. Security notes

- **Control** can stop/remove containers and run compose; protect with `DASHBOARD_CONTROL_TOKEN`.
- **Docs API** only serves whitelisted files under `PROJECT_ROOT` (no path traversal).

## 9. Smoke tests

```bash
./cloudflare-local/scripts/smoke.sh
```

Requires Traefik routing `*.lh` to backends on port 80.

## 10. LEco DevOps + Hosted apps (maintainers)

When changing registration, materialization, or compose behavior, treat these as one system:

- **Effective manifest:** bridge (`leco.app.yaml`) + profile **`infrastructure`** (`leco.yaml`). Dashboard listing and control paths must resolve compose the same way as **`leco-devops`** (`dashboard/leco_control.py`, `tools/deploy-cli/leco_app/schema.py`).
- **Read-only `wsp:` paths:** **`source`** symlink target and **config symlinks** (`configRefs`, `runtimes[].config`, wrangler scan; host `/workspace-parent` remap) live in **`dashboard/leco_detect.py`**, **`dashboard/leco_wrangler_paths.py`**, **`dashboard/leco_materialize.py`**, **`dashboard/hosting_layout.py`**.
- **Teardown:** **`dashboard/control.py`** + **`dashboard/hosted_offboard.py`** — offboard after **`leco-devops down`** even on failure.
- **Attached services:** **`dashboard/hosted_app_services.py`** builds snapshot `attached_services` (compose + runtimes + CF + labeled host/Docker connection URIs). Operator help: **`docs/help/12-hosted-app-attached-services.md`**; API/fields: **`docs/help/dev-08-hosted-app-services.md`**.
- **Cloud VM platform:** Requirements **`docs/SRS_CLOUD_VM_PLATFORM.md`**; runbook **`docs/CLOUD_VM_DEPLOYMENT.md`**, isolation **`docs/DEV_STACK_ISOLATION.md`**; `config/leco-platform.yaml`, install profiles, Platform tab, `/api/platform/*`, `/api/dev-stacks/*`, `platform.devStackId` in schema.

Operator-facing map: **[LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md)**. Dashboard **Docs** catalog: **`dashboard/docs_catalog.py`** (`leco-app-blueprint` id).
