# Hosted app — attached services panel

On **Hosted apps → [your app]**, the **Attached services** section lists every dependency this application uses in local dev: Docker Compose services, edge runtimes (Workers/Pages), Cloudflare-local bindings, and shared ecosystem data stores when Wrangler or compose references them.

## What you see

Services are grouped:

| Group | Contents |
|-------|----------|
| **Data stores** | MySQL, PostgreSQL, Redis, MongoDB, MinIO from compose (or ecosystem hubs when the service name matches infra) |
| **Edge runtimes** | Each `infrastructure.runtimes[]` entry with Traefik URL and container name |
| **Cloudflare local** | KV / R2 / D1 bindings from `leco.local-cf.yaml` or Wrangler preview |
| **Compose services** | Other containers (nginx, Node API, proxies, …) |

Each card can include:

- **Credentials** — user, password, database (from compose `environment`, `.env`, or the UI credential vault for ecosystem hubs)
- **Connection strings** — labeled **host** vs **Docker DNS** (see below)
- **Management UI** — Adminer, MongoDB Compass (host), MinIO console, KV/R2/D1 panels, app URLs
- **Auto-login** — for ecosystem services in the UI vault (MySQL/Postgres via Adminer, MinIO console)

## Connection strings: host vs Docker

For **MySQL**, **PostgreSQL**, **Redis**, **MongoDB**, and **MinIO**, each data-store card shows **two kinds** of URIs when LEco can infer them:

| Label | When to use | Example |
|-------|-------------|---------|
| **From your Mac (host)** | Compass, TablePlus, `redis-cli`, IDE DB plugins on the workstation | `mongodb://127.0.0.1:27017/botfeed` |
| **From your Mac (*.lh DNS)** | Same tools when you prefer ecosystem DNS (requires `*.lh` → loopback) | `mysql://app:pw@mysql.lh:3306/appdb` |
| **From app containers (Docker DNS)** | Values in `server` / `worker` env (`MONGODB_URI`, `REDIS_URL`, …) — **only** resolvable inside the compose network | `mongodb://mongo:27017/botfeed` |

**Rules of thumb:**

1. **On your Mac** — use a row labeled **host** or **\*.lh** (green accent in the UI). The hostname `mongo`, `redis`, or `db` will **not** resolve outside Docker.
2. **Inside a container** — use **Docker DNS** (amber accent): the compose **service name** as hostname (`mongo`, `redis`, `n8n_postgres`, …).
3. **Published ports** — host URIs use `127.0.0.1:<port>` from compose `ports:` (e.g. `27017:27017`). If no port is published, only Docker DNS rows appear.
4. **App env hints** — when `MONGODB_URI` / `REDIS_URL` appear on `server` or workers, LEco lists the Docker URI and adds a matching host URI when a port mapping exists.

**MongoDB Compass:** use **MongoDB Compass (host)** or copy the **From your Mac (host)** string — not `mongodb://mongo:27017/`.

## Data source

The panel is built when you open or refresh the app detail pane (`GET /api/hosted-apps/<slug>/snapshot` → `attached_services`). Logic lives in `dashboard/hosted_app_services.py`.

| Source | How it is detected |
|--------|-------------------|
| Compose | Same `-f` file list as `leco-devops deploy`; services merged from all compose files |
| Runtimes | `leco.yaml` → `infrastructure.runtimes[]` + routing hostnames |
| Cloudflare | `wranglerBindingPreview`, `leco.local-cf.yaml`, Wrangler TOML scan |
| Ecosystem MySQL/Postgres | Compose service name (`mysql`, `n8n_postgres`) or Wrangler `[[hyperdrive]]` |

## Local development only

Credentials are shown **in plain text** for operator convenience on `*.lh`. They reflect compose defaults and `config/ui-credentials.yaml` — not production secrets. Do not use this panel as a production secret store.

## Limitations

- Production Wrangler secrets are not fetched from Cloudflare.
- Gitignored `.env` files may be unreadable from the dashboard process — some fields may be missing.
- Shared ecosystem databases appear when bindings or compose names match; LEco does not statically analyze all application source code.
- Host URIs assume compose **port publishing** to the machine; internal-only services have no `127.0.0.1` row.

## Related

- [Hosted apps (dashboard)](help:hosted-apps)
- [UI credential vault](../UI_CREDENTIAL_VAULT.md)
- [Multi-Wrangler monorepos](help:multi-wrangler-monorepo)
- [Attached services (developers)](help:dev-hosted-app-services)
