# Deploy your own application — Workers, Docker, NGINX, Node.js

This guide explains how to run **additional applications** on the local ecosystem: extend the **Workers (Miniflare)** runtime, add **Docker** services on `lh-network`, front them with **Traefik** (`*.lh`), and use **NGINX**, **Node.js**, and existing **databases / caches**.

It complements [DEVOPS_GUIDE.md](DEVOPS_GUIDE.md) (KV, R2, D1, compose operations) and [SETUP.md](SETUP.md) (DNS, TLS, first boot).

---

## 1. How traffic reaches your app

1. **Browser / client** uses a hostname such as `https://myapp.lh` (resolve `*.lh` to your machine — see SETUP).
2. **Traefik** (containers `traefik`, ports 80/443) terminates TLS with `certs/wildcard.lh.pem` and routes by `Host(...)`.
3. Traefik forwards to a **backend URL** on **`lh-network`**, e.g. `http://my-node-api:3000`.

**Requirements for any new HTTP service**

- Container (or process) listens on a port **inside** `lh-network`.
- Container is attached to **`lh-network`** (same as Traefik).
- **`traefik/dynamic.yml`** defines a **router** (hostname) and **service** (backend URL).
- The file provider uses **`watch: true`** (`traefik/traefik-static.yaml`), so saving `dynamic.yml` usually **reloads routes without restarting Traefik**.

**Optional:** Add your container name to **`NETWORK_CONTAINERS`** in `ecosystem-stack/core.sh` so `./ecosystem-stack/ecosystem-stack.sh repair-network` reconnects it after Docker restarts.

---

## 2. Inventory — all routed services and stacks

Use this table to see **what already exists**, **which hostname** to use, and **how it is started**.

| Public host (`*.lh`) | Backend (Docker DNS) | Container | Port (internal) | Started via |
|---------------------|----------------------|-----------|-----------------|-------------|
| `ai.lh` | `http://open-webui:8080` | `open-webui` | 8080 | `ecosystem-stack/services/webui.sh` |
| `n8n.lh` | `http://n8n:5678` | `n8n` | 5678 | `ecosystem-stack/services/n8n.sh` |
| `ollama.lh` | `http://ollama:11434` | `ollama` | 11434 | `ecosystem-stack/services/ollama.sh` |
| `localhost.lh` | `http://service-dashboard:8090` | `service-dashboard` | 8090 | `ecosystem-stack/services/dashboard.sh` |
| `traefik.lh` | Traefik API (`api@internal`) | `traefik` | API on 8080 (published) | `ecosystem-stack/services/traefik.sh` |
| `r2.lh` | `http://r2-adapter:8081` | `r2-adapter` | 8081 | `cloudflare-local/docker-compose.yml` |
| `kv.lh` | `http://kv-adapter:8082` | `kv-adapter` | 8082 | same |
| `d1.lh` | `http://d1-adapter:8083` | `d1-adapter` | 8083 | same |
| `autoscale.lh` | `http://autoscaler:8084` | `autoscaler` | 8084 | same |
| `s3.lh` | `http://minio:9000` | `minio` | 9000 | same |
| `minio-console.lh` | `http://minio:9001` | `minio` | 9001 | same |
| `workers.lh` | `http://workers-runtime:8787` | `workers-runtime` | 8787 | same |
| `browser.lh` | `http://browser-rendering-local:8085` | `browser-rendering-local` | 8085 | same |
| `cache.lh` | `http://cache-varnish:80` | `cache-varnish` → `cache-nginx` | 80 | `infra/docker-compose.yml` |
| `mail.lh` | `http://mailpit:8025` | `mailpit` | 8025 | same |
| `telegram.lh` | `http://telegram-gateway:8091` | `telegram-gateway` | 8091 | same |
| `adminer.lh` | `http://adminer:8080` | `adminer` | 8080 | same |
| `redis-ui.lh` | `http://redis-commander:8081` | `redis-commander` | 8081 | same |

**Docker services on `lh-network` without their own `*.lh` router**

| Container | Role | Reach it |
|-----------|------|----------|
| `valkey` | Redis-compatible backend for KV adapter | `valkey:6379` from containers; host `127.0.0.1:6380` (published) |
| `autoscale-demo` | Nginx replicas scaled by autoscaler | Internal `http://autoscale-demo:80` (not individually exposed on Traefik) |
| `open-webui`, `n8n`, `ollama`, etc. | Ecosystem stack | Only via Traefik rows above unless you publish extra ports in scripts |

**Compose / scripts (high level)**

| Area | Path | Control / CLI |
|------|------|----------------|
| Traefik + TLS | `traefik/`, `certs/` | `./ecosystem-stack/services/traefik.sh` |
| Ecosystem stack (WebUI, Ollama, n8n, Postgres, dashboard) | `ecosystem-stack/services/*.sh` | `./ecosystem-stack/ecosystem-stack.sh …` |
| Cloudflare-local (MinIO, Valkey, adapters, Workers, browser, autoscaler) | `cloudflare-local/docker-compose.yml` | `./ecosystem-stack/services/cloudflare-local.sh` |
| Infra (MySQL, Redis, Mailpit, cache lab, Adminer, …) | `infra/docker-compose.yml` | `./ecosystem-stack/services/infra.sh` |

**Databases (two different Postgres instances)**

- **n8n Postgres:** container `n8n_postgres`, user `postgres` / password `password`, DB `n8n`, host **`n8n_postgres:5432`** from other containers on `lh-network`. Host port **5432** is published for local tools.
- **Infra MySQL:** container `mysql`, defaults `root` / `localdev`, DB `localdev`, host **`mysql:3306`** on `lh-network`. Host port **3306** published.

**Redis / Valkey**

- **Infra Redis** (general apps): `redis:6379` on `lh-network` (no host port in compose — use Traefik UI or `docker exec`).
- **KV stack (CF-local):** `valkey:6379` inside Docker; host **`127.0.0.1:6380`** maps to Valkey for non-Docker clients.

---

## 3. Pattern A — Deploy logic on the **Workers** runtime (Miniflare)

**Use when:** you want a **single JavaScript fetch handler** (Workers-like) without maintaining a separate Node server image.

**Where:** `cloudflare-local/adapters/workers-runtime/worker.js`  
**Public URL:** `https://workers.lh` (already routed).

**Steps**

1. Edit `handleRequest` in `worker.js` (routes, JSON, proxy calls to `http://kv.lh`, `http://r2.lh`, etc.).
2. Rebuild the container:

   ```bash
   docker compose -f cloudflare-local/docker-compose.yml up -d --build workers-runtime
   ```

3. Check: `curl -fsS https://workers.lh/health` (or `http://workers.lh/health`).

**Separate hostname for a second “worker”:** Miniflare in this repo exposes **one** worker. For another hostname, use **Pattern B** (your own Node or NGINX container) or run a **second** compose service that builds another Miniflare image on a different port and add a new router in `dynamic.yml`.

---

## 4. Pattern B — New **Docker** app + **Traefik** host (generic)

**Use when:** any stack (Node, Go, Python, static file server) that listens on HTTP inside the network.

### 4.1 Run a one-off container (quick test)

```bash
docker network create lh-network 2>/dev/null || true
docker run -d --name my-echo --network lh-network nginx:alpine
# Point Traefik at http://my-echo:80 (see below)
```

### 4.2 Add routers and services in `traefik/dynamic.yml`

Duplicate an existing pair (HTTP + HTTPS). Example for `myapp.lh` → `my-node-api:3000`:

```yaml
    myapp-http:
      rule: "Host(`myapp.lh`)"
      service: myapp-service
      entryPoints:
        - web

    myapp-https:
      rule: "Host(`myapp.lh`)"
      service: myapp-service
      entryPoints:
        - websecure
      tls: true
```

Under `http.services`:

```yaml
    myapp-service:
      loadBalancer:
        servers:
          - url: "http://my-node-api:3000"
```

Ensure **`myapp.lh`** resolves (same as other `*.lh` hosts). Traefik should pick up changes automatically.

### 4.3 Long-lived service: **infra** or **dedicated compose**

- **Infra-adjacent** (MySQL, Redis, Mailpit neighbors): add a service under **`infra/docker-compose.yml`** (see Pattern C).
- **Next to CF-local:** add under **`cloudflare-local/docker-compose.yml`** if it depends on MinIO/Valkey adapters often.

Always use:

```yaml
networks:
  lh-network:
    external: true
```

---

## 5. Pattern C — **Node.js** API on **infra** (with MySQL / Redis)

**Use when:** you need a normal Node process, npm dependencies, and easy access to **infra MySQL** and **infra Redis**.

### 5.1 Layout (example)

```text
infra/my-api/
  Dockerfile
  package.json
  server.js
```

**`Dockerfile`** (listen on `3000`):

```dockerfile
FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev
COPY . .
EXPOSE 3000
ENV NODE_ENV=production
CMD ["node", "server.js"]
```

**`server.js`** — use Docker DNS names:

- MySQL: `mysql:3306` (user/password/DB from compose env defaults unless you changed them).
- Redis: `redis:6379`.

### 5.2 Register the service in `infra/docker-compose.yml`

```yaml
  my-api:
    restart: unless-stopped
    build:
      context: ./my-api
    container_name: my-api
    environment:
      PORT: "3000"
      DATABASE_URL: mysql://root:localdev@mysql:3306/localdev
      REDIS_URL: redis://redis:6379
    depends_on:
      - mysql
      - redis
    networks:
      - lh-network
```

Then:

```bash
docker compose -f infra/docker-compose.yml up -d --build my-api
```

Add **Traefik** routes (Pattern B) pointing to `http://my-api:3000`, and add **`my-api`** to `NETWORK_CONTAINERS` in `ecosystem-stack/core.sh` if you want repair-network to include it.

---

## 6. Pattern D — **NGINX** (static site or reverse proxy)

### 6.1 Cache lab (existing)

- **Nginx** container: `cache-nginx` (static `index.html` under `infra/cache-nginx/`).
- **Varnish** in front: `cache-varnish`; public host **`cache.lh`** hits Varnish → Nginx.
- Customize by editing `infra/cache-nginx/` and rebuilding:  
  `docker compose -f infra/docker-compose.yml up -d --build cache-nginx`

### 6.2 Dedicated static site on its own hostname

1. Create `infra/my-site/` with `Dockerfile`:

   ```dockerfile
   FROM nginx:alpine
   COPY public/ /usr/share/nginx/html/
   ```

2. Add service to `infra/docker-compose.yml` on `lh-network`.
3. Add `Host(`mysite.lh`)` routers in `traefik/dynamic.yml` to `http://my-site:80`.

### 6.3 NGINX as reverse proxy to another container

Use `proxy_pass http://backend:PORT;` in a custom `nginx.conf`, mount it in the image or via volume, and route Traefik to **NGINX’s** port. Keeps TLS termination at Traefik; optional path-based routing can stay in NGINX.

---

## 7. Pattern E — **PostgreSQL (n8n)** and **Cloudflare-local** APIs

- **n8n’s Postgres** (`n8n_postgres`): use from custom apps on `lh-network` with host `n8n_postgres`, port `5432`, credentials from `postgres.sh` (default user `postgres`, password `password`, database `n8n`). Prefer a **separate database** or schema if you do not want to collide with n8n metadata — create DBs with `psql` or Adminer connected to that host (from a container on the same network, or via published 5432).

- **HTTP APIs** (no Workers bindings): call **`http://r2.lh`**, **`http://kv.lh`**, **`http://d1.lh`** from your app using server-side `fetch` or HTTP clients. From inside Docker, you can also use internal names if you add routes only on the adapter containers (e.g. `http://r2-adapter:8081`) — same paths as public docs in [DEVOPS_GUIDE.md](DEVOPS_GUIDE.md).

---

## 8. Autoscale demo (NGINX replicas)

- **`autoscale-demo`:** `nginx:alpine` with labels for the autoscaler (`autoscale.group`, `autoscale.target`).
- **`autoscaler`:** Docker-socket access; scales replicas of the demo group. Public API: **`autoscale.lh`**.
- **Template:** copy the label pattern and autoscaler env to experiment with your own stateless containers (advanced — read `cloudflare-local/autoscaler` and compose comments).

---

## 9. Operations checklist

| Step | Action |
|------|--------|
| Network | `docker network create lh-network` (scripts usually create it). |
| TLS | `*.lh` cert in `certs/`; Traefik mounts `certs` read-only. |
| Route | Edit `traefik/dynamic.yml` (router + service + `tls: true` on websecure). |
| DNS | `myapp.lh` → loopback (or your dev host IP). |
| After Docker daemon restart | `./ecosystem-stack/ecosystem-stack.sh repair-network` |
| Logs | `docker logs -f <container>` or Dashboard **Control** tab |
| Rebuild | `docker compose … up -d --build <service>` |

---

## 10. **LEco DevOps** (`leco-devops`)

For **third-party** repos (many apps, no edits to `ecosystem-stack` / `core.sh`), use **LEco DevOps** in `tools/deploy-cli/`: the **`leco-devops`** command writes `leco.app.yaml`, runs `docker compose` deploy/stop/logs/status, optional **`wrangler deploy`**, and prints **Traefik** YAML fragments for manual paste.

**Install:** `cd tools/deploy-cli` (from repo root), then `pip install -e .` — not from `tools/` alone.

See [DEPLOY_CLI.md](DEPLOY_CLI.md) and [tools/deploy-cli/README.md](../tools/deploy-cli/README.md).

---

## 11. Related documentation

- [DEVOPS_GUIDE.md](DEVOPS_GUIDE.md) — Workers, KV, R2, D1 APIs, compose reference  
- [DEPLOYMENT.md](DEPLOYMENT.md) — backups, bulk lifecycle  
- [DEPLOY_CLI.md](DEPLOY_CLI.md) — LEco DevOps install and commands  
- [DEVELOPMENT_PLAYBOOK.md](DEVELOPMENT_PLAYBOOK.md) — repo layout, security notes  
- Dashboard **Documentation** tab — same files when the repo is mounted at `/project`  
- Dynamic CLI mirror: **Service management commands** (Operations category)
