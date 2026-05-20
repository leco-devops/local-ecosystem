# DevOps guide — deployment, Workers, KV, R2, and D1 (local ecosystem)

This document is for **operators and DevOps engineers** deploying and using the **local-ecosystem** stack: Docker services, Traefik, **LEco DevOps**, and **Cloudflare-local** emulation (R2-, KV-, D1-, Workers-style APIs). It complements:

| Document | Role |
|----------|------|
| [DEPLOY_CUSTOM_APPS.md](DEPLOY_CUSTOM_APPS.md) | **Your apps:** Workers vs new containers, Traefik, NGINX, Node on infra, full hostname inventory |
| [DEPLOY_CLI.md](DEPLOY_CLI.md) | **leco-devops** CLI: `leco.app.yaml`, compose + Wrangler, Traefik fragments (plug-and-play) |
| [LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md) | **Architecture map:** v3 manifest merge, `hosting/` materialization, dashboard vs CLI, maintainer code pointers |
| [SETUP.md](SETUP.md) | First-time install: DNS, TLS, Docker, mkcert |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Day-two: start/stop, bulk actions, backups |
| [cloudflare-local/docs/USER_MANUAL.md](../cloudflare-local/docs/USER_MANUAL.md) | CF-local URLs and dashboard notes |
| [cloudflare-local/docs/ARCHITECTURE.md](../cloudflare-local/docs/ARCHITECTURE.md) | Component topology |

**Important:** These APIs are **local development emulations**. They are **not** Cloudflare production bindings. Behavior and limits differ from Workers + R2 + KV + D1 on the edge.

---

## 1. What you are deploying

| Layer | Components |
|-------|------------|
| **Edge / routing** | Traefik (`*.lh`), TLS from `certs/` |
| **Ecosystem stack** | Traefik, Open WebUI, Ollama, Postgres (n8n), n8n, dashboard |
| **Cloudflare-local** | MinIO, Valkey, `r2-adapter`, `kv-adapter`, `d1-adapter`, `workers-runtime`, autoscaler, browser-rendering, etc. |
| **Infra** | MySQL, Redis, Mailpit, Adminer, cache lab, Telegram gateway (optional) |

Shared Docker network: **`lh-network`** (external to compose files; created by stack scripts).

---

## 2. Deploying on a worker or build host

Use any Linux/macOS machine with **Docker Engine + Compose v2** and enough disk/RAM for images.

### 2.1 Clone and prerequisites

```bash
git clone https://github.com/leco-devops/local-ecosystem.git local-ecosystem
cd local-ecosystem
```

Complete **DNS** (`*.lh` → loopback), **mkcert** TLS, and **Docker** as in [SETUP.md](SETUP.md).

### 2.2 Full platform start (recommended)

From the **repository root**:

```bash
./ecosystem-stack/ecosystem-stack.sh start
```

This follows **`START_ORDER`** in `ecosystem-stack/core.sh` (Traefik → Postgres → Ollama → WebUI → n8n → dashboard → cloudflare-local → infra) and runs **`repair-network`** so containers attach to **`lh-network`**.

### 2.3 Redeploy after code changes

| Goal | Command |
|------|---------|
| Rebuild **only** the dashboard | `./ecosystem-stack/services/dashboard.sh deploy` |
| Rebuild **only** Cloudflare-local images | `./ecosystem-stack/services/cloudflare-local.sh start` (uses `compose up -d --build`) |
| Recreate **one** compose service | `./ecosystem-stack/services/cloudflare-local.sh recreate workers-runtime` |
| **Everything** (stop phase skips dashboard for in-flight Control API) | `./ecosystem-stack/ecosystem-stack.sh deploy` |

### 2.4 CI / headless worker pattern

1. Install Docker, create `lh-network` if missing: `docker network create lh-network`.
2. Run `./ecosystem-stack/ecosystem-stack.sh start` (or compose files directly with the same network).
3. Health gates (examples):
   - `curl -fsS http://localhost:8090/` (dashboard, if published)
   - `curl -fsS http://r2.lh/health` (after Traefik + CF-local up; requires `*.lh` or `curl --resolve`)
4. Set **`DASHBOARD_CONTROL_TOKEN`** on the dashboard container if automation calls Control APIs.

For hosts **without** `*.lh` DNS, use **`curl --resolve`**, **`Host` headers**, or published ports (see service scripts and compose files).

---

## 3. Workers runtime (local Miniflare)

**URL:** `http://workers.lh` / `https://workers.lh` (via Traefik)  
**Container:** `workers-runtime`  
**Source:** `cloudflare-local/adapters/workers-runtime/worker.js`

### 3.1 Deploy / rebuild after editing the worker

```bash
docker compose -f cloudflare-local/docker-compose.yml up -d --build workers-runtime
# or
./ecosystem-stack/services/cloudflare-local.sh recreate workers-runtime
```

### 3.2 Verify

```bash
curl -fsS http://workers.lh/health
curl -fsS http://workers.lh/
```

### 3.3 Extending the worker

- Implement routes in **`handleRequest`** (service-worker style: `fetch` event).
- **`GET /panel`** serves a small HTML explorer.
- **No** production Workers bindings (`env.KV`, `env.R2`, etc.) are wired here; call **HTTP APIs** to `kv.lh`, `r2.lh`, `d1.lh` from your handler if you need storage.

---

## 4. R2 (S3-style object storage)

**Public adapter:** `http://r2.lh` / `https://r2.lh`  
**Adapter panel:** `http://r2.lh/panel` — buckets, credentials, explorer  
**Direct S3 API (MinIO):** `http://s3.lh` / `https://s3.lh` (Traefik → MinIO `:9000`)  
**MinIO console:** `http://minio-console.lh`

### 4.1 Default credentials (compose)

Typical dev defaults (override in compose or env):

- **Access key:** `minioadmin`
- **Secret key:** `minioadmin`

Use the same values for **S3 clients** talking to `s3.lh` or for the **r2-adapter** → MinIO backend.

### 4.2 R2 adapter HTTP API (summary)

Base URL: **`http://r2.lh`** (or internal `http://r2-adapter:8081` on `lh-network`).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + backend check |
| GET | `/buckets` | List buckets (JSON) |
| POST | `/buckets` | Create bucket (JSON body, see adapter) |
| DELETE | `/buckets/<name>` | Delete bucket |
| GET | `/objects/<bucket>?prefix=&limit=` | List objects |
| PUT | `/objects/<bucket>/<key>` | Upload object (body = bytes) |
| GET | `/objects/<bucket>/<key>` | Download object |
| DELETE | `/objects/<bucket>/<key>` | Delete object |
| POST | `/multipart/start`, `/multipart/complete` | Start / finish multipart (JSON body) |
| PUT | `/multipart/upload-part?upload_id=&part_number=` | Upload one part (body = bytes) |
| POST | `/presign` | Presigned-style URL helper (dev) |

**Example — create bucket and upload**

```bash
curl -fsS -X POST http://r2.lh/buckets -H 'Content-Type: application/json' -d '{"name":"demo"}'
echo hello | curl -fsS -X PUT http://r2.lh/objects/demo/hello.txt --data-binary @-
curl -fsS http://r2.lh/objects/demo/hello.txt
```

### 4.3 AWS CLI against `s3.lh`

```bash
export AWS_ACCESS_KEY_ID=minioadmin
export AWS_SECRET_ACCESS_KEY=minioadmin
aws --endpoint-url http://s3.lh s3 ls
```

Use **path-style** or virtual-host as your client requires; TLS uses local mkcert.

---

## 5. KV (Valkey / Redis-style namespaces)

**URL:** `http://kv.lh` / `https://kv.lh`  
**Panel:** `http://kv.lh/panel`  
**Backend:** Valkey (container `valkey`); adapter uses **`KV_REDIS_URL`** (default `redis://valkey:6379/0` inside Docker).

**From the Mac/Linux host** (published port in `cloudflare-local/docker-compose.yml`): **`127.0.0.1:6380`** maps to Valkey `6379` inside the network (use `redis-cli` or app clients; HTTP remains `kv.lh`).

### 5.1 KV adapter HTTP API (summary)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Ping Valkey, namespace count |
| GET | `/namespaces` | List namespaces |
| POST | `/namespaces` | Create namespace JSON `{"name":"..."}` |
| DELETE | `/namespaces/<namespace>` | Delete namespace |
| PUT | `/namespaces/<namespace>/values/<key>` | Set value (body = raw string or JSON per adapter) |
| GET | `/namespaces/<namespace>/values/<key>` | Get value |
| DELETE | `/namespaces/<namespace>/values/<key>` | Delete key |
| GET | `/namespaces/<namespace>/keys?prefix=&limit=` | List keys (default limit 200) |

**Example**

```bash
curl -fsS -X POST http://kv.lh/namespaces -H 'Content-Type: application/json' -d '{"name":"app"}'
curl -fsS -X PUT http://kv.lh/namespaces/app/values/user:1 -H 'Content-Type: text/plain' -d 'alice'
curl -fsS http://kv.lh/namespaces/app/values/user:1
```

**Note:** This is **not** Cloudflare Workers `KV` binding semantics; it is an HTTP façade over Redis key prefixes for local dev.

---

## 6. D1 (SQLite databases)

**URL:** `http://d1.lh` / `https://d1.lh`  
**Panel:** `http://d1.lh/panel` — create DBs, run `SELECT`  
**Auth:** None in default local stack (dev only).

### 6.1 Common API paths

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Status |
| GET | `/databases` | List database names |
| POST | `/databases` | Create DB JSON `{"name":"..."}` |
| DELETE | `/databases/<name>` | Delete database file |
| POST | `/databases/<name>/query` | Read-only style: `SELECT` → JSON rows |
| POST | `/databases/<name>/execute` | Mutating SQL (`INSERT`/`UPDATE`/`DELETE`) → `rows_affected` |
| POST | `/databases/<name>/migrate` | Apply `*.sql` from migrations dir (see adapter) |
| POST | `/databases/<name>/backup` | Backup file (see response body) |
| POST | `/databases/<name>/restore` | Restore from uploaded backup |

**Example**

```bash
curl -fsS http://d1.lh/databases
curl -fsS -X POST http://d1.lh/databases -H 'Content-Type: application/json' -d '{"name":"app"}'
curl -fsS -X POST http://d1.lh/databases/app/query \
  -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT sqlite_version();","params":[]}'
```

Data lives in the **`d1-adapter`** Docker volume; **`reset`** on the CF-local stack can wipe volumes — see [DEPLOYMENT.md](DEPLOYMENT.md).

---

## 7. Other local “edge” services (short)

| Service | URL | Notes |
|---------|-----|--------|
| Autoscaler API | `http://autoscale.lh` | Demo scaling policy + `autoscale-demo` |
| Browser rendering | `http://browser.lh` | Playwright/Chromium; see `BROWSER_RENDERING_LOCAL.md` in Docs |
| R2 panel / health | `http://r2.lh/panel`, `/health` | Operational UI |

---

## 8. Traefik and `*.lh`

- Dynamic routes: **`traefik/dynamic.yml`** (mounted into the Traefik container).
- Each service is usually exposed on **HTTP (80)** and **HTTPS (443)** with mkcert wildcard `*.lh`.
- Dashboard ops UI: **`http://localhost.lh`** (Traefik → `service-dashboard:8090`).

If a route returns **502**, confirm the target container is on **`lh-network`** and healthy: `./ecosystem-stack/ecosystem-stack.sh repair-network`.

---

## 9. LEco DevOps — Documentation tab

The in-app **Documentation** tab reads Markdown from the repo mounted at **`DASHBOARD_PROJECT_ROOT`** (default **`/project`** in the container). This file appears when:

- The LEco DevOps container is started with **`-v "$REPO:/project:rw"`** (see `ecosystem-stack/services/dashboard.sh`), and  
- The module is listed in **`dashboard/docs_catalog.py`**.

Use it for:

- Browsing this guide and linked manuals without leaving the UI  
- **Service management commands** (dynamic doc generated from Control targets)

---

## 10. Checklist before production (if you reuse patterns elsewhere)

- [ ] Replace default MinIO / DB passwords; restrict compose ports on shared hosts.  
- [ ] Do **not** expose the D1 adapter or LEco DevOps without auth on untrusted networks.  
- [ ] Use real Cloudflare R2/KV/D1/Workers in production — this stack is for **local parity**, not a production control plane.  
- [ ] Back up volumes you care about (Postgres, D1, MinIO) per [DEPLOYMENT.md](DEPLOYMENT.md).

---

## 11. Quick reference — compose and scripts

```bash
# Cloudflare-local stack
docker compose -f cloudflare-local/docker-compose.yml ps
docker compose -f cloudflare-local/docker-compose.yml up -d --build

# Infra stack
docker compose -f infra/docker-compose.yml up -d --build

# Dashboard rebuild
./ecosystem-stack/services/dashboard.sh deploy
```

**Control API** (optional token): `POST /api/control` and `POST /api/control/stream` — same targets as the **Control** tab; see dynamic doc **Service management commands** in the Documentation tab.
