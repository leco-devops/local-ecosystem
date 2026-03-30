# Development playbook — Local Ecosystem

This guide helps you **extend**, **debug**, and **ship** changes across the AI stack, Traefik, Cloudflare-local adapters, and the ops dashboard.

## 1. Repository map (where things live)

| Area | Path | Purpose |
|------|------|---------|
| AI stack orchestration | `ai-stack/core.sh`, `ai-stack/ai-stack.sh` | Start order, network repair, service scripts |
| Per-service Docker scripts | `ai-stack/services/*.sh` | `start` / `stop` / `build` for each container |
| Traefik dynamic routes | `traefik/dynamic.yml` | `*.lh` host rules (duplicate in `ai-stack/config/dynamic.yml` if you use that tree) |
| Cloudflare local | `cloudflare-local/docker-compose.yml`, `cloudflare-local/adapters/*` | R2, KV, D1, Workers, autoscaler |
| Ops dashboard | `dashboard/` | Flask app, metrics, control API, UI |
| TLS | `certs/` | mkcert wildcard for `*.lh` |

## 2. Daily commands

```bash
# Full stack (interactive menu)
./ai-stack/ai-stack.sh menu

# One service
./ai-stack/ai-stack.sh restart dashboard
./ai-stack/ai-stack.sh logs cloudflare-local

# Cloudflare stack only (from repo root)
./ai-stack/services/cloudflare-local.sh start
./ai-stack/services/cloudflare-local.sh recreate r2-adapter
./ai-stack/services/cloudflare-local.sh backup
```

## 3. After changing dashboard code

The dashboard runs in Docker. Rebuild the image:

```bash
./ai-stack/ai-stack.sh restart dashboard
# or: docker build -t local/service-dashboard:latest dashboard && docker rm -f service-dashboard && …
```

The container should mount **`$PROJECT_ROOT:/project`** so Control actions and in-dashboard docs can read the repo. The run script uses **`--restart unless-stopped`** so the dashboard comes back when the Docker daemon restarts (unless the container was explicitly stopped).

The **Docs** tab includes a generated module **Service management commands** (`service-management`) built from `control_targets.py` (per-service CLI aligned with Control).

## 4. Adding a new `*.lh` service

1. **Container**: create or extend a `ai-stack/services/<name>.sh` script (or add a compose service on `lh-network`).
2. **Traefik**: add `routers` + `services` in `traefik/dynamic.yml` pointing at `http://<container>:<port>`.
3. **DNS**: ensure `something.lh` resolves (dnsmasq / etc.) to the host running Traefik.
4. **Dashboard** (optional): add URLs to `monitor.py` `SERVICE_MAP` for probes and to the static URL catalog in `dashboard/reference_data.py` (or rely on SERVICE_MAP-driven encyclopedia).

## 5. Adding a Cloudflare-local adapter

1. New folder under `cloudflare-local/adapters/<name>/` with `Dockerfile` + app.
2. Register service in `cloudflare-local/docker-compose.yml` on `lh-network`.
3. Traefik route + `CLOUDFLARE_ENDPOINTS` / `collect_cloudflare_local_status()` in `dashboard/monitor.py` if you want health tiles.
4. `dashboard/control.py` `CF_TARGETS` if you want Control actions.
5. `ai-stack/core.sh` `NETWORK_CONTAINERS` for network repair.

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
| `/api/control` | POST | `{ "target_id", "action", "token"? }` — includes `stack-ecosystem-all` (`start` \| `stop` \| `restart` \| `deploy`) which runs `bulk_ecosystem` in `ai-stack/core.sh` |
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
