# Deployment and operations — Local Ecosystem

This guide covers **starting, updating, stopping, and debugging** the stack after initial setup. For first-time installation, see **[SETUP.md](SETUP.md)**.

---

## 1. Entry points

| Tool | Path | Use when |
|------|------|----------|
| Interactive menu | `./ecosystem-stack/ecosystem-stack.sh menu` | Exploring services |
| CLI | `./ecosystem-stack/ecosystem-stack.sh <action> [service]` | Scripts and CI |
| Foundation installer | `./ecosystem-stack/install-foundation.sh` | First run: dependency checks + guided service selection |
| Per-service script | `./ecosystem-stack/services/<name>.sh <action>` | Direct control of one unit |
| Dashboard **Control** tab | http://localhost.lh | Platform targets (ecosystem stack, infra, CF-local) with optional token |
| Dashboard **Hosted apps** tab | http://localhost.lh | Per registry id: metrics, logs, compose controls (`leco-stack-<id>`). Rows come from **`config/leco-registry.yaml`** — finish **Register** after **Save YAML**. If compose metadata fails to load (e.g. missing optional `additionalComposeFilesFromManifest` files), fix paths in **`leco.yaml`** or add the overlay file beside **`leco.app.yaml`**. |

Always run commands from the **repository root** (or pass absolute paths). The variable **`PROJECT_ROOT`** is inferred from each service script’s location.

---

## 2. Service actions

Most **`ecosystem-stack/services/*.sh`** scripts support:

| Action | Typical effect |
|--------|----------------|
| `start` | Create network if needed, build/run container(s) |
| `deploy` | Same as `start` for scripts that define it (e.g. dashboard rebuilds the image every time) |
| `stop` | Stop container(s) |
| `restart` | `stop` then `start` |
| `remove` | Remove container (data volumes depend on script) |
| `reset` | Aggressive teardown (may delete volumes — read script) |
| `pause` / `unpause` | Docker pause (not all stacks) |
| `status` | `docker ps` or compose `ps` |
| `logs` | Follow logs |

**Traefik (`traefik.sh`):** besides the usual actions, **`ensure-hosting-files`** refreshes **`hosting/traefik/01-stack-core.yml`** from **`traefik/dynamic.yml`**, ensures **`hosting/traefik/dynamic.yml`** exists, and normalizes invalid empty **`http`** stubs (Traefik v3). **`heal`** does that and **restarts** the Traefik container if it already exists (safe recovery after Docker Desktop watcher issues). From the stack CLI: **`./ecosystem-stack/ecosystem-stack.sh heal traefik`**.

**Dashboard (`dashboard.sh`):** **`start` / `deploy`** rebuild the Docker image from **`dashboard/`** and recreate **`service-dashboard`**. After a successful container start, **`dashboard.sh`** runs **`traefik.sh heal`** unless **`DASHBOARD_SKIP_TRAEFIK_HEAL=1`** is set (repairs **`hosting/traefik/*`** and restarts Traefik when running). **`deploy`** skips heal during **`start`** and runs **`heal`** once at the end so Traefik is not restarted twice. On **macOS**, **`stop`** and **`remove`** also **uninstall** the host CPU metrics LaunchAgent (see SETUP.md).

**Dashboard control token:** If **`DASHBOARD_CONTROL_TOKEN`** is **unset**, the Control API and Hosted apps / Routes mutations do not require a token, and the UI does not block those actions for a missing browser token. If **`DASHBOARD_CONTROL_TOKEN`** is **set**, the browser must send that value (Control tab **Save** persists it in `localStorage`). For **trusted local / single-user** setups only, **`DASHBOARD_INJECT_CONTROL_TOKEN_UI=1`** (or `true` / `yes`) embeds the same token in the main dashboard HTML and seeds `localStorage` on each load so you do not type it manually — **do not enable on internet-exposed dashboards** (the token is visible in page source and DevTools). Example extras on `docker run`: `-e DASHBOARD_CONTROL_TOKEN=…` and `-e DASHBOARD_INJECT_CONTROL_TOKEN_UI=1`. See comments in **`ecosystem-stack/services/dashboard.sh`**.

**Docker Compose from the dashboard (Docker Desktop):** Hosted **Deploy** runs `docker compose` inside `service-dashboard` with the host socket. Bind-mount sources must be paths the **daemon** knows (host file sharing). The dashboard image mounts the workspace parent at **`/workspace-parent`** and again at its **host absolute path**, and sets **`LECO_WORKSPACE_PARENT_HOST`** / **`LECO_PROJECT_ROOT_HOST`**. **LEco DevOps** remaps compose `-f`, `--env-file`, and **cwd** so volume paths resolve on the host. Redeploy the dashboard after changing `dashboard.sh`.

**Hosted apps / read-only `wsp:` paths:** Sibling repos are mounted read-only at **`/workspace-parent`**. **Register** cannot write `leco.app.yaml` there. The dashboard **materializes** under **`hosting/app-available/<slug>/`** (writable `/project`), adds a **`source`** symlink to the real app tree so compose/wrangler paths keep working, and registers **`hosting/app-available/<slug>/leco.app.yaml`** in **`config/leco-registry.yaml`**. See **`hosting/README.md`** and **`docs/LECO_APP_BLUEPRINT.md`**. **Zip upload:** `POST /api/hosted/upload-zip` (multipart **`file`**, form **`app_id`** or **`slug`**, control token in header or form) extracts into **`hosting/app-available/<slug>/`** with zip-slip checks, max **200 MiB**, then **deletes the uploaded zip**. Use **Detect** / **Register** with path **`hosting/app-available/<slug>`** (or materialize flow) after uploading.

**Hosted apps — Remove / Reset:** the dashboard runs **`leco-devops ecosystem-unregister`**, which runs **local CF teardown** first when enabled (so in-project **`leco-local-*`** adapters are still up), then **`docker compose down`** ( **`down -v`** on **Reset** ) when the manifest has compose and the compose file exists, then Traefik cleanup when applicable, registry removal, and removal of **`hosting/app-available`** when the manifest was under hosting. If **`down`** fails, unregister still proceeds. Extra compose files: **`infrastructure.dockerCompose.additionalComposeFiles`** in **`leco.yaml`** — see **`docs/DEPLOY_CLI.md`**.

**Cloudflare-local (`cloudflare-local.sh`):** wraps **`docker compose`**; see script for **`recreate`**, **`backup`**, etc.

---

## 3. Starting everything

```bash
cd /path/to/local-ecosystem
./ecosystem-stack/ecosystem-stack.sh start
```

Start order is defined in **`ecosystem-stack/core.sh`** (`START_ORDER`). **`repair-network`** runs after bulk start/restart so listed containers join **`lh-network`** and get **`--restart unless-stopped`** where applicable.

---

## 4. Stopping and “stop all”

### 4.1 One service

```bash
./ecosystem-stack/ecosystem-stack.sh stop traefik
./ecosystem-stack/ecosystem-stack.sh stop dashboard
```

### 4.2 All services (CLI: `run_all`)

```bash
./ecosystem-stack/ecosystem-stack.sh stop
```

This runs **`stop`** on **every** service under **`ecosystem-stack/services/*.sh`** (order follows the filesystem glob, not `START_ORDER`). **The dashboard is included**, so **`service-dashboard`** stops and, on **macOS**, **`dashboard.sh`** uninstalls the host CPU metrics LaunchAgent.

### 4.3 Dashboard Control API vs CLI

The **`bulk_ecosystem`** helper in **`core.sh`** (dashboard **Control** → **`stack-ecosystem-all`**) walks services in **reverse `START_ORDER`** for teardown-style actions but **always skips `dashboard`** so the HTTP request can finish. It also skips **`traefik`** and **`postgres`** by default so the reverse proxy and shared DB stay available. That applies to bulk **`stop`**, **`pause`**, **`remove`**, **`reset`**, and to the teardown half of **`restart`**, **`deploy`**, and **`recreate`**.

Set **`ECOSYSTEM_BULK_PLATFORM_SKIP`** to a space-separated list of service names to change the default (`traefik postgres`). If **`ECOSYSTEM_BULK_PLATFORM_SKIP`** is unset, **`ECOSYSTEM_BULK_PAUSE_SKIP`** is still read for backward compatibility. After **`restart`**, **`deploy`**, or **`recreate`**, the start phase runs in forward order but **does not re-run `start`** on a platform service that is **still running** (many service scripts recreate the container on every `start`).

**`bulk_ecosystem start`** and **`unpause`** still run across **every** service in order (full start / unpause).

To stop only the dashboard from the shell:

```bash
./ecosystem-stack/ecosystem-stack.sh stop dashboard
```

### 4.4 Core infra — shell when you need Traefik, Postgres, or dashboard

Bulk Control actions intentionally avoid tearing down **Traefik** (routing), **Postgres** (`n8n_postgres`), and the **dashboard** container. Use the CLI from the **repository root** when you need to operate on those directly:

| Goal | Command |
|------|---------|
| Traefik — status | `./ecosystem-stack/ecosystem-stack.sh status traefik` |
| Traefik — restart | `./ecosystem-stack/ecosystem-stack.sh restart traefik` |
| Traefik — stop / start | `./ecosystem-stack/ecosystem-stack.sh stop traefik` · `./ecosystem-stack/ecosystem-stack.sh start traefik` |
| Traefik — direct script | `./ecosystem-stack/services/traefik.sh restart` |
| Traefik — repair hosting files + restart if running | `./ecosystem-stack/services/traefik.sh heal` or `./ecosystem-stack/ecosystem-stack.sh heal traefik` |
| Postgres — status | `./ecosystem-stack/ecosystem-stack.sh status postgres` |
| Postgres — restart | `./ecosystem-stack/ecosystem-stack.sh restart postgres` |
| Postgres — stop / start | `./ecosystem-stack/ecosystem-stack.sh stop postgres` · `./ecosystem-stack/ecosystem-stack.sh start postgres` |
| Postgres — reset (drops data volume) | `./ecosystem-stack/ecosystem-stack.sh reset postgres` |
| Dashboard — rebuild image + recreate | `./ecosystem-stack/ecosystem-stack.sh deploy dashboard` or `./ecosystem-stack/services/dashboard.sh deploy` |
| Dashboard — restart (same rebuild path as deploy) | `./ecosystem-stack/ecosystem-stack.sh restart dashboard` |
| Dashboard — stop | `./ecosystem-stack/ecosystem-stack.sh stop dashboard` |

All scripts under **`ecosystem-stack/services/*.sh`** are tracked **executable** (`100755`) and start with **`#!/usr/bin/env bash`** so direct paths such as **`./ecosystem-stack/services/dashboard.sh deploy`**, **`./ecosystem-stack/services/traefik.sh restart`**, and **`./ecosystem-stack/services/cloudflare-local.sh start`** work on a fresh clone. (`ecosystem-stack.sh` still **`source`**s them — the shebang line is a comment when sourced.) If you see **permission denied**, run **`bash ecosystem-stack/services/<name>.sh …`** or **`chmod +x ecosystem-stack/services/<name>.sh`**.

**LEco DevOps + Traefik on Docker Desktop (macOS):** The LEco DevOps container mounts the repo at `/project` and talks to the host Docker daemon via the socket. `docker run -v …` bind sources must be **host** paths (e.g. `/Users/you/.../local-ecosystem`), not `/project/...`. `dashboard.sh` sets `DASHBOARD_DOCKER_BIND_ROOT` automatically; Traefik’s script uses it for `/traefik` and `/certs` mounts. If Traefik was created **before** this (or the env is missing), remove the old container (`docker rm -f traefik`) and start again from the host or **Control** — do not only “Start” the broken container in Docker Desktop. Ensure **Docker Desktop → Settings → Resources → File sharing** includes your repo directory if it lives outside the default allowed paths.

**Full stack without bulk safeguards** (stops **every** service, including dashboard and Traefik): `./ecosystem-stack/ecosystem-stack.sh stop` with no service name (see § 4.2).

### 4.5 Pause / unpause

```bash
./ecosystem-stack/ecosystem-stack.sh pause
./ecosystem-stack/ecosystem-stack.sh unpause
```

**CLI** `pause` / `unpause` with **no service name** walks **all** `services/*.sh` (like **`stop`**), **including the dashboard**. The dashboard **Control** bulk **`pause`** path uses **`bulk_ecosystem`** (same skips as § 4.3). Bulk **`unpause`** still runs on **every** service in start order so anything paused individually is resumed.

---

## 5. Restart, deploy, recreate

```bash
./ecosystem-stack/ecosystem-stack.sh restart              # each service in START_ORDER: that service's restart (includes dashboard)
./ecosystem-stack/ecosystem-stack.sh restart dashboard    # dashboard only (rebuild + recreate)
./ecosystem-stack/ecosystem-stack.sh deploy dashboard     # same as restart for dashboard
```

**`bulk_ecosystem`** (dashboard **Control** → **stack-ecosystem-all**):

| Action | Behavior |
|--------|----------|
| `start` | Start all in `START_ORDER` + repair network |
| `stop` | Stop in reverse order; **skips dashboard** + default platform (`traefik`, `postgres`; see `ECOSYSTEM_BULK_PLATFORM_SKIP`) |
| `pause` | Same skips as `stop` |
| `unpause` | Unpause every service in `START_ORDER` |
| `restart` / `deploy` | Teardown phase (same skips as `stop`), then start forward; **skips `start`** for a default platform service if it is **still running** |
| `remove` / `reset` | Same reverse skips as `stop` |
| `recreate` | Remove phase (same skips), then same conditional start as `restart` |

CLI **`./ecosystem-stack/ecosystem-stack.sh stop`** (no service) does **not** use `bulk_ecosystem`; it stops **every** service. Per-service and core-infra commands are in **section 4.4**.

---

## 6. Deploying dashboard changes

After editing Python, templates, or static assets under **`dashboard/`**:

```bash
./ecosystem-stack/services/dashboard.sh deploy
# or
./ecosystem-stack/ecosystem-stack.sh restart dashboard
```

The Dockerfile always rebuilds the image on **`start`/`deploy`**.

---

## 7. Traefik and routing changes

Traefik reads **`/etc/traefik-dynamic`** (repo: **`hosting/traefik/`**), configured in **`traefik/traefik-static.yaml`** with **`watch: true`**.

| File | Role |
|------|------|
| **`traefik/dynamic.yml`** | Canonical stack routes in **git**. Edited here for platform services. |
| **`hosting/traefik/01-stack-core.yml`** | **Copy** of the above, rewritten on every **`traefik.sh start`** — must not be a symlink (see SETUP.md). |
| **`hosting/traefik/dynamic.yml`** | **Writable** merge layer for **`leco-devops`** / LEco DevOps Routes; empty document should be **`{}`**, not **`http: {}`** (Traefik v3 rejects the latter). |

1. Change platform routes in **`traefik/dynamic.yml`**, then **`./ecosystem-stack/ecosystem-stack.sh restart traefik`** (or any **`traefik.sh start`**) so **`01-stack-core.yml`** is recopied.
2. Hosted-app merges go to **`hosting/traefik/dynamic.yml`**; saves usually **reload without** restarting Traefik.
3. If **`*.lh`** returns Traefik’s minimal **404 page not found**, run **`./ecosystem-stack/services/traefik.sh heal`** and inspect **`docker logs traefik`** for `file.Provider` / YAML errors.
4. Keep **`ecosystem-stack/config/dynamic.yml`** in sync if your workflow uses that copy.

```bash
./ecosystem-stack/ecosystem-stack.sh restart traefik
./ecosystem-stack/services/traefik.sh heal
```

---

## 8. Cloudflare-local deployment

```bash
./ecosystem-stack/ecosystem-stack.sh start cloudflare-local
./ecosystem-stack/ecosystem-stack.sh logs cloudflare-local
```

Single-service recreate (example):

```bash
./ecosystem-stack/services/cloudflare-local.sh recreate r2-adapter
```

Full compose rebuild:

```bash
docker compose -f cloudflare-local/docker-compose.yml up -d --build
```

---

## 9. Network repair

If you see **502 Bad Gateway** or containers not reachable by Traefik host rules:

```bash
./ecosystem-stack/ecosystem-stack.sh repair-network
```

This ensures **`lh-network`** exists, connects known containers (see **`NETWORK_CONTAINERS`** in **`core.sh`**), and applies **`docker update --restart unless-stopped`** on existing containers.

---

## 10. Ollama model pulls

```bash
./ecosystem-stack/ecosystem-stack.sh ollama-pull-models
```

Requires a running **`ollama`** container.

---

## 11. Security: Control API token

Optional environment variable **`DASHBOARD_CONTROL_TOKEN`** restricts **Control** tab and some model actions. Set it in the environment used to run **`docker run`** for the dashboard (see **`dashboard.sh`**), or send **`X-Control-Token`** from the UI.

---

## 12. Troubleshooting

| Symptom | Things to check |
|---------|------------------|
| `*.lh` does not resolve | dnsmasq running; `/etc/resolver/lh`; `ping ai.lh` |
| TLS warnings | mkcert CA installed; correct cert files in **`certs/`** |
| 502 from Traefik | Target container on **`lh-network`**; **`repair-network`**; Traefik **`loadBalancer`** host matches Docker DNS (**`container_name`** or **`{project}-{service}-1`**); merged **`hosting/traefik/dynamic.yml`**. See **[HOSTED_APPS_TRAEFIK_RUNBOOK.md](HOSTED_APPS_TRAEFIK_RUNBOOK.md)**. |
| Hosted app UI works, dashboard URL row **“HTTP 0”** or API probe odd | Fixed: internal `*.lh` probes must use **`http://traefik`** + **`Host`** header (not **`https://traefik`**). Restart **LEco DevOps** after upgrading `dashboard/monitor.py`. Details in runbook. |
| SPA on **`*.lh`** still calls **`localhost`** for API | Upstream compose **`REACT_APP_*` / `VITE_*`** pointing at host ports; use **`docker-compose.leco-hosting.yml`** to set same-origin **`*.lh`** (see **`DEPLOY_CLI.md`**, runbook §1). |
| Traefik **404 page not found** on all `*.lh` | File provider failed: **`traefik.sh heal`** or **`restart`**; **`docker logs traefik`**; avoid symlinks for **`01-stack-core.yml`**; no standalone **`http: {}`** in **`hosting/traefik/dynamic.yml`** |
| Dashboard empty metrics | Docker socket mounted; time for samples to accumulate |
| macOS CPU temp flat / missing | **`~/.local-eco-host-metrics/cpu_temp_c.txt`**; LaunchAgent status; **`powermetrics`** sudo / NOPASSWD |
| n8n cookie / HTTPS issues | Env in **`n8n.sh`** (`N8N_TRUST_PROXY`, `N8N_SECURE_COOKIE`) |
| Java / mkcert | **`unset JAVA_HOME`** and retry **`mkcert -install`** |

**Logs:**

```bash
./ecosystem-stack/ecosystem-stack.sh logs n8n
./ecosystem-stack/ecosystem-stack.sh logs traefik
docker logs service-dashboard
```

**macOS host metrics writer logs:** `~/Library/Logs/local-ecosystem-host-cpu-temp.*.log`

---

## 13. Optional smoke tests

Cloudflare-local HTTP checks (expects Traefik routing to backends):

```bash
./cloudflare-local/scripts/smoke.sh
```

---

## 14. Documentation index

| Document | Purpose |
|----------|---------|
| [SETUP.md](SETUP.md) | First-time full setup |
| [DEVELOPMENT_PLAYBOOK.md](DEVELOPMENT_PLAYBOOK.md) | Development workflow and APIs |
| [HOSTED_APPS_TRAEFIK_RUNBOOK.md](HOSTED_APPS_TRAEFIK_RUNBOOK.md) | Hosted apps: Traefik 502, probes, compose overlays |
| [../README.md](../README.md) | Overview and quick links |
