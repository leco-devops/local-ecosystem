# Cloudflare Local — User Manual

Platform-wide install (DNS, TLS, Traefik, ecosystem stack): **[../../docs/SETUP.md](../../docs/SETUP.md)**. Operations: **[../../docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md)**.

## Prerequisites

- Docker with Compose v2
- Network `lh-network` — created automatically when you start any ecosystem-stack service or Cloudflare-local; **`./ecosystem-stack/ecosystem-stack.sh repair-network`** also creates it, reconnects all known containers, and sets **`restart: unless-stopped`**-style policy on running containers (see `ecosystem-stack/core.sh`).
- Optional: Traefik routes in `traefik/dynamic.yml` (and mirror in `ecosystem-stack/config/dynamic.yml` if you use that layout)

## Start and stop

From the repo root, using the ecosystem stack helper:

```bash
./ecosystem-stack/ecosystem-stack.sh start cloudflare-local
./ecosystem-stack/ecosystem-stack.sh stop cloudflare-local
./ecosystem-stack/ecosystem-stack.sh logs cloudflare-local
```

Or run the service script directly (also works without sourcing `core.sh`):

```bash
./ecosystem-stack/services/cloudflare-local.sh start
./ecosystem-stack/services/cloudflare-local.sh stop
./ecosystem-stack/services/cloudflare-local.sh recreate r2-adapter
./ecosystem-stack/services/cloudflare-local.sh backup
```

`backup` calls `http://d1.lh/databases` and issues `POST /databases/<name>/backup` for each database. Override the base URL if needed:

```bash
D1_PUBLIC_URL=http://d1-adapter:8083 ./ecosystem-stack/services/cloudflare-local.sh backup
```

*(When run on the host, use `http://d1.lh`; from inside a container on `lh-network`, use the internal URL.)*

## URLs (with Traefik)

| URL | Purpose |
|-----|---------|
| http://r2.lh | R2-style adapter |
| http://kv.lh | KV adapter |
| http://d1.lh | D1 adapter |
| http://workers.lh | Workers runtime (Miniflare) |
| http://autoscale.lh | Autoscaler API |
| http://minio-console.lh | MinIO UI |

Full binding mapping: [docs/CF_LECO_SERVICE_MAP.md](../../docs/CF_LECO_SERVICE_MAP.md).

## Workers runtime

- Source: `cloudflare-local/adapters/workers-runtime/worker.js` (service-worker style for Miniflare 2).
- Rebuild after edits: `docker compose -f cloudflare-local/docker-compose.yml up -d --build workers-runtime`.
- Health: `GET http://workers.lh/health` (or `/`).

## LEco DevOps (localhost.lh)

1. **Overview** — Managed services, Cloudflare Local status, quick charts.
2. **Infrastructure** — Deeper layout including **Ollama models**: pinned list from `ecosystem-stack/config/ollama-pinned-models.txt`, installed models, and actions (pull one, pull all pinned, delete, unload in-memory). Uses `GET /api/ollama/models` and `POST /api/ollama/models/action` when a control token is configured.
3. **Metrics** — Line charts for all running containers (aggregated): CPU, memory %, network Mb/s, block Mb/s, estimated IOPS (assuming 4 KiB ops), Docker-tracked disk, and Docker RAM as % of engine-reported host memory. Recent history is cached in the browser (**localStorage**) and shown immediately on load or if the metrics API is temporarily unavailable.
4. **Control** — Start, stop, restart, remove, pause, unpause, deploy, recreate, reset, and backup (where defined). Requires the LEco DevOps container mount of **`$PROJECT_ROOT` → `/project`** (enabled in `ecosystem-stack/services/dashboard.sh`). When an action finishes, LEco DevOps reloads targets and overview data in the background (no separate “refresh cards” control). At the top of the tab, **Start all / Stop all / Restart all / Redeploy all** runs every `ecosystem-stack/services/*.sh` unit in bulk via `bulk_ecosystem` in `ecosystem-stack/core.sh` (stop phases skip the LEco DevOps container so the HTTP request can complete).

**Browser persistence:** the last active tab and cached overview/metrics snapshots (up to ~48 hours) are restored on reload so the UI is usable before the next successful API round-trip.

### Optional control token

Set on the LEco DevOps container:

```text
DASHBOARD_CONTROL_TOKEN=your-secret
```

Then use the **Control** tab “Save in browser” field or send header `X-Control-Token`.

### Where backups go

- **D1**: Inside the `d1-adapter` volume `d1_backups` (see adapter API responses for file paths).
- **PostgreSQL (n8n)**: From the dashboard control action, files are written under `/project/.local-eco-backups/` on the host.

## Reset vs remove

- **remove** (stack): `docker compose down` — drops containers but keeps named volumes unless you prune them.
- **reset** (stack): `docker compose down -v` — **deletes** MinIO, Valkey, and D1 volume data. Use only when you intend to wipe local data.
