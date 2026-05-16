# Sample: Node.js multi-process + Varnish + MongoDB + Redis

Use this pattern when onboarding a **multi-process Node.js application** that has:

- Multiple entry scripts (server, worker, cron, queue consumer, etc.)
- Hardcoded `localhost` URIs in a config file (MongoDB, Redis, Varnish, etc.)
- An HTTP cache layer (Varnish) in front of the Express server
- Production service configs (VCL, nginx.conf, redis.conf) that need Docker adaptation

## Architecture

```
Client → Traefik (my-app.lh) → Varnish (:80) → Express (:3000) → MongoDB / Redis
                                                 ├── worker.js   → MongoDB / Redis
                                                 └── cron.js     → MongoDB / Redis
```

## Files

| File | Purpose |
|------|---------|
| `leco.app.yaml` | Bridge — registry slug, root, profile pointer, config refs |
| `leco.yaml` | Profile — compose config, Traefik routing, healthcheck, URL probes |
| `docker-compose.yml` | Base stack — services, volumes, networks, healthchecks |
| `docker-compose.leco-hosting.yml` | Hosting overlay — lh-network, preloader mount, env vars, commands |
| `leco-docker-preload.js` | Runtime config patcher — intercepts `require('./config')` |
| `conf/varnish/default.vcl` | Docker-adapted Varnish VCL (or copy your production VCL here) |

## Quick start

**Option A — CLI scaffold (recommended):**

```bash
leco-devops scaffold myapp -E /path/to/local-ecosystem \
  --source-path /abs/path/to/upstream/repo \
  --health-path /alb-health-check
```

This copies all template files, replaces `my-app` → `myapp` throughout (container names, volumes, hostnames, network), and prints a next-steps checklist. Use `--dry-run` to preview.

**Option B — manual copy:**

1. Copy this directory to `hosting/app-available/<your-slug>/`
2. Edit every file — replace `my-app` with your slug, adjust paths and service names
3. Copy your production VCL to `conf/varnish/default.vcl` and apply Docker adaptations (see comments in file)

**Then register and deploy:**

4. Open the dashboard → **Register application** → select your app → **Register**
5. Hit **Recreate** from the control panel

Or from the CLI:

```bash
leco-devops ecosystem-register --cwd hosting/app-available/myapp -E /path/to/local-ecosystem --merge-traefik
leco-devops deploy --cwd hosting/app-available/myapp
```

## Key patterns

### Runtime config preloading (`leco-docker-preload.js`)

Instead of modifying upstream source code, the preloader intercepts Node's `require('./config')` at startup and patches the returned object using `LECO_*` environment variables. This means:

- **Zero upstream changes** — the app repo stays clean
- **All config is in docker-compose.leco-hosting.yml** — easy to audit
- **Logs each patch** — `[leco-preload] MONGODB_URI → mongodb://mongo:27017/`

### Custom service configs (`conf/` directory)

Production configs live in `conf/<service>/<config-file>` and are bind-mounted `:ro` into containers. When adapting a production config for Docker:

1. Replace `localhost` / `127.0.0.1` with the Docker `container_name`
2. Update ACLs for Docker bridge subnets (`172.16.0.0/12`)
3. Remove host-based routing (single backend in Docker)
4. Use `vcl 4.1;` for Varnish 7.x (required for `resp.body`, `bereq.backend` assignment)

### Multi-process shared node_modules

The primary service (`server`) runs `npm install` (skipped on restart when `node_modules/express` exists) which populates the shared `app_node_modules` volume. Secondary services (`worker`, `cron`) poll for `/app/node_modules/express` before starting.

### MongoDB host access (Compass / mongosh)

The `mongo` service publishes **`27018:27017`** by default so Compass on your Mac can reach the **container** volume. Do not map host port **27017** if you already run native Mongo on the Mac — that port is a different database.

- **Inside containers:** `mongodb://mongo:27017/` (set via `LECO_MONGO_URI` in the hosting overlay).
- **From your Mac:** `mongodb://127.0.0.1:27018/<database>` — use the host port from compose `ports:` (see LEco **Attached services**).
- **No publish / empty volume:** `docker exec -it <container> mongosh <database>` or `mongodump` from Mac → `mongorestore` via `docker exec`.

Set `LECO_MONGO_DATABASE` in the hosting overlay when the app uses a fixed database name.

### Varnish 503 prevention (required for this template)

| Pattern | Why |
|---------|-----|
| **`server` healthcheck** on port 3000 | Compose marks the API healthy only when Express is listening |
| **`varnish` `depends_on: server: service_healthy`** | Varnish does not accept traffic until the backend is up |
| **`LECO_DISABLE_VARNISH_NCSA: "true"`** | Node must not run host-side `sudo varnishncsa` inside the server container |
| **`LECO_VARNISH_HOST: varnish`** | PURGE/BAN targets the Varnish container, not localhost |
| **Skip apt-get/npm on restart** | Chromium and `node_modules` persist in the container filesystem between restarts |

Upstream apps that start a `varnishWorker` / `varnishncsa` log monitor must guard that path when `LECO_VARNISH_HOST` is set (see botfeed `varnish.js`). **403** after the stack is healthy usually means missing MongoDB `client_conf_key` data — not a proxy failure.

### Seed MongoDB / Redis data

After deploy, copy production-like data into the stack:

```bash
# One database (Mac → LEco container)
mongodump --uri="mongodb://localhost:27017" --db=<source-database> --archive \
  | mongorestore --uri="mongodb://127.0.0.1:<host-port>/<target-database>" --archive --drop

# Full server (all databases — omit --db)
mongodump --uri="mongodb://localhost:27017" --archive \
  | mongorestore --uri="mongodb://127.0.0.1:<host-port>" --archive --drop
```

Or place dumps under `data/mongo/` and use **Hosted apps → Import data** — see [Seed data import](../../docs/help/13-hosted-app-data-import.md).

See [docs/help/09-503-varnish-backend.md](../../docs/help/09-503-varnish-backend.md).

### Staging (offload)

The dashboard **staging** button tears down all containers and volumes (`docker compose down -v --remove-orphans`) and strips Traefik routes, but keeps all files in `hosting/app-available/`. Hit **Recreate** to bring it back.

From the CLI (mirrors the dashboard staging button):

```bash
leco-devops offload --cwd hosting/app-available/myapp -E /path/to/local-ecosystem
```

## Customisation checklist

- [ ] `leco.app.yaml` — `name`, `configRefs.packageJson`
- [ ] `leco.yaml` — `projectName`, `hostname`, `backendHost`, `backendPort`, `healthcheckUrls`, `publicUrl`
- [ ] `docker-compose.yml` — source path, `container_name` prefix, volume names, images
- [ ] `docker-compose.leco-hosting.yml` — `LECO_*` env vars, `LECO_OWN_DOMAINS`, `LECO_DISABLE_VARNISH_NCSA`, command scripts
- [ ] `docker-compose.yml` — `server` healthcheck path matches your app (`/health` or `/alb-health-check`)
- [ ] `leco-docker-preload.js` — config key names to match your app's `config.js` exports
- [ ] `conf/varnish/default.vcl` — backend `.host` and `.port`, ACL, VCL logic
