# Cloudflare Local — Implementation guide

**Environment setup:** [../../docs/SETUP.md](../../docs/SETUP.md) · **Deploy / ops:** [../../docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md).

End-to-end wiring from **Docker** → **Traefik** → **browser** and how **LEco DevOps** observes the stack.

## 1. Network model

- All user-facing containers join **`lh-network`** (external in compose, created by the ecosystem stack).
- Traefik runs on the same network and forwards `Host: *.lh` to upstream containers.
- The LEco DevOps container uses the **Docker socket** to inspect all containers and, when `/project` is mounted, runs **`docker compose`** against `cloudflare-local/docker-compose.yml`.

## 2. Compose layout (`cloudflare-local/docker-compose.yml`)

| Service | Container name | Internal port | Traefik host |
|---------|----------------|---------------|--------------|
| minio | minio | 9000 (S3), 9001 (console) | `minio-console.lh` → 9001 |
| valkey | valkey | 6379 | (internal) |
| r2-adapter | r2-adapter | 8081 | `r2.lh` |
| kv-adapter | kv-adapter | 8082 | `kv.lh` |
| d1-adapter | d1-adapter | 8083 | `d1.lh` |
| workers-runtime | workers-runtime | 8787 | `workers.lh` |
| autoscaler | autoscaler | 8084 | `autoscale.lh` |
| autoscale-demo | autoscale-demo | 80 | (scaled replicas) |

Volumes: `minio_data`, `valkey_data`, `d1_data`, `d1_backups`; migrations bind-mounted read-only into `d1-adapter`.

## 3. Traefik configuration

File: **`traefik/dynamic.yml`** (and mirror **`ecosystem-stack/config/dynamic.yml`** if Traefik loads that copy).

Each router uses `rule: Host(\`name.lh\`)` and an entryPoint (`web` for HTTP). TLS certificates reference `certs/wildcard.lh*.pem` where configured.

After editing, reload Traefik or restart the Traefik container.

## 4. Adapters — HTTP surface

### R2 (`r2-adapter`)

- Talks to MinIO with S3 API.
- Health: `GET /health`.
- Typical flows: buckets, objects (see adapter `app.py`).

### KV (`kv-adapter`)

- Backed by Valkey.
- Health: `GET /health`.
- Namespaces and key paths under `/namespaces/...`.

### D1 (`d1-adapter`)

- SQLite files under `/data`, backups under `/backups`.
- `GET /databases`, `POST /databases`, `POST /databases/<name>/backup`, etc.

### Workers (`workers-runtime`)

- Miniflare 2 runs `worker.js` (service worker `fetch` handler).
- `GET /` and `GET /health` return JSON status.

### Autoscaler

- Docker socket mounted; reads CPU for labeled containers and scales `autoscale-demo` replicas.
- `GET /status` for policy and metrics snapshot.

## 5. Dashboard integration

### Probes

`dashboard/monitor.py` **`SERVICE_MAP`** lists containers and **HTTP URLs** probed via Traefik (`Host` header to `traefik` upstream). This drives:

- Managed service cards
- Aggregate health counts
- URL encyclopedia health merge (same probe data)

### Cloudflare tile

`collect_cloudflare_local_status()` calls adapter HTTP APIs on the **internal** Docker DNS names (`http://r2-adapter:8081`, etc.) so it works even if Traefik is down.

### Metrics time series

`dashboard/timeseries.py` aggregates **stats for all running containers** (CPU, memory, network deltas, block I/O deltas). Sampled on overview refresh and when opening **Metrics** (`/api/metrics/history`).

### Control plane

`dashboard/control.py` whitelists:

- Compose services for Cloudflare stack
- Ecosystem stack service scripts under `/project/ecosystem-stack/services/*.sh`

Requires **Docker CLI + compose plugin** in the dashboard image and **`/project`** mount.

## 6. Scripts

| Script | Role |
|--------|------|
| `cloudflare-local/scripts/bootstrap.sh` | Bring up stack |
| `cloudflare-local/scripts/seed.sh` | Seed demo data |
| `cloudflare-local/scripts/smoke.sh` | Curl checks through Traefik |
| `ecosystem-stack/services/cloudflare-local.sh` | `start`, `deploy`, `recreate`, `backup`, direct CLI |

## 7. Failure modes

| Symptom | Check |
|---------|--------|
| `*.lh` 404 | Traefik dynamic file, container on `lh-network`, Host rule |
| Adapter unreachable in dashboard CF tile | Container up, same network as `service-dashboard` |
| URL encyclopedia “down” but app works | Probe uses Traefik; fix routing or TLS mismatch |
| Control “compose file missing” | Mount repo to `/project` in dashboard `docker run` |
| Workers tab down | `workers-runtime` image build, compose service, Miniflare logs |

## 8. Related docs

- [ARCHITECTURE.md](./ARCHITECTURE.md) — conceptual map
- [USER_MANUAL.md](./USER_MANUAL.md) — operator tasks
- [../README.md](../README.md) — quick start and URLs
