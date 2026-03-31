# Deployment and operations — Local Ecosystem

This guide covers **starting, updating, stopping, and debugging** the stack after initial setup. For first-time installation, see **[SETUP.md](SETUP.md)**.

---

## 1. Entry points

| Tool | Path | Use when |
|------|------|----------|
| Interactive menu | `./ai-stack/ai-stack.sh menu` | Exploring services |
| CLI | `./ai-stack/ai-stack.sh <action> [service]` | Scripts and CI |
| Per-service script | `./ai-stack/services/<name>.sh <action>` | Direct control of one unit |
| Dashboard **Control** tab | http://localhost.lh | Platform targets (AI stack, infra, CF-local) with optional token |
| Dashboard **Hosted apps** tab | http://localhost.lh | Per registered leco app: metrics, charts, logs, insights, compose controls (`leco-stack-<id>`) |

Always run commands from the **repository root** (or pass absolute paths). The variable **`PROJECT_ROOT`** is inferred from each service script’s location.

---

## 2. Service actions

Most **`ai-stack/services/*.sh`** scripts support:

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

**Dashboard (`dashboard.sh`):** **`start` / `deploy`** rebuild the Docker image from **`dashboard/`** and recreate **`service-dashboard`**. On **macOS**, **`stop`** and **`remove`** also **uninstall** the host CPU metrics LaunchAgent (see SETUP.md).

**Cloudflare-local (`cloudflare-local.sh`):** wraps **`docker compose`**; see script for **`recreate`**, **`backup`**, etc.

---

## 3. Starting everything

```bash
cd /path/to/local-ecosystem
./ai-stack/ai-stack.sh start
```

Start order is defined in **`ai-stack/core.sh`** (`START_ORDER`). **`repair-network`** runs after bulk start/restart so listed containers join **`lh-network`** and get **`--restart unless-stopped`** where applicable.

---

## 4. Stopping and “stop all”

### 4.1 One service

```bash
./ai-stack/ai-stack.sh stop traefik
./ai-stack/ai-stack.sh stop dashboard
```

### 4.2 All services (CLI: `run_all`)

```bash
./ai-stack/ai-stack.sh stop
```

This runs **`stop`** on **every** service under **`ai-stack/services/*.sh`** (order follows the filesystem glob, not `START_ORDER`). **The dashboard is included**, so **`service-dashboard`** stops and, on **macOS**, **`dashboard.sh`** uninstalls the host CPU metrics LaunchAgent.

### 4.3 Dashboard Control API vs CLI

The **`bulk_ecosystem`** helper in **`core.sh`** (dashboard **Control** → **`stack-ecosystem-all`**) walks services in **reverse `START_ORDER`** for teardown-style actions but **always skips `dashboard`** so the HTTP request can finish. It also skips **`traefik`** and **`postgres`** by default so the reverse proxy and shared DB stay available. That applies to bulk **`stop`**, **`pause`**, **`remove`**, **`reset`**, and to the teardown half of **`restart`**, **`deploy`**, and **`recreate`**.

Set **`ECOSYSTEM_BULK_PLATFORM_SKIP`** to a space-separated list of service names to change the default (`traefik postgres`). If **`ECOSYSTEM_BULK_PLATFORM_SKIP`** is unset, **`ECOSYSTEM_BULK_PAUSE_SKIP`** is still read for backward compatibility. After **`restart`**, **`deploy`**, or **`recreate`**, the start phase runs in forward order but **does not re-run `start`** on a platform service that is **still running** (many service scripts recreate the container on every `start`).

**`bulk_ecosystem start`** and **`unpause`** still run across **every** service in order (full start / unpause).

To stop only the dashboard from the shell:

```bash
./ai-stack/ai-stack.sh stop dashboard
```

### 4.4 Core infra — shell when you need Traefik, Postgres, or dashboard

Bulk Control actions intentionally avoid tearing down **Traefik** (routing), **Postgres** (`n8n_postgres`), and the **dashboard** container. Use the CLI from the **repository root** when you need to operate on those directly:

| Goal | Command |
|------|---------|
| Traefik — status | `./ai-stack/ai-stack.sh status traefik` |
| Traefik — restart | `./ai-stack/ai-stack.sh restart traefik` |
| Traefik — stop / start | `./ai-stack/ai-stack.sh stop traefik` · `./ai-stack/ai-stack.sh start traefik` |
| Traefik — direct script | `./ai-stack/services/traefik.sh restart` |
| Postgres — status | `./ai-stack/ai-stack.sh status postgres` |
| Postgres — restart | `./ai-stack/ai-stack.sh restart postgres` |
| Postgres — stop / start | `./ai-stack/ai-stack.sh stop postgres` · `./ai-stack/ai-stack.sh start postgres` |
| Postgres — reset (drops data volume) | `./ai-stack/ai-stack.sh reset postgres` |
| Dashboard — rebuild image + recreate | `./ai-stack/ai-stack.sh deploy dashboard` or `./ai-stack/services/dashboard.sh deploy` |
| Dashboard — restart (same rebuild path as deploy) | `./ai-stack/ai-stack.sh restart dashboard` |
| Dashboard — stop | `./ai-stack/ai-stack.sh stop dashboard` |

**Ops dashboard + Traefik on Docker Desktop (macOS):** The dashboard container mounts the repo at `/project` and talks to the host Docker daemon via the socket. `docker run -v …` bind sources must be **host** paths (e.g. `/Users/you/.../local-ecosystem`), not `/project/...`. `dashboard.sh` sets `DASHBOARD_DOCKER_BIND_ROOT` automatically; Traefik’s script uses it for `/traefik` and `/certs` mounts. If Traefik was created **before** this (or the env is missing), remove the old container (`docker rm -f traefik`) and start again from the host or **Control** — do not only “Start” the broken container in Docker Desktop. Ensure **Docker Desktop → Settings → Resources → File sharing** includes your repo directory if it lives outside the default allowed paths.

**Full stack without bulk safeguards** (stops **every** service, including dashboard and Traefik): `./ai-stack/ai-stack.sh stop` with no service name (see § 4.2).

### 4.5 Pause / unpause

```bash
./ai-stack/ai-stack.sh pause
./ai-stack/ai-stack.sh unpause
```

**CLI** `pause` / `unpause` with **no service name** walks **all** `services/*.sh` (like **`stop`**), **including the dashboard**. The dashboard **Control** bulk **`pause`** path uses **`bulk_ecosystem`** (same skips as § 4.3). Bulk **`unpause`** still runs on **every** service in start order so anything paused individually is resumed.

---

## 5. Restart, deploy, recreate

```bash
./ai-stack/ai-stack.sh restart              # each service in START_ORDER: that service's restart (includes dashboard)
./ai-stack/ai-stack.sh restart dashboard    # dashboard only (rebuild + recreate)
./ai-stack/ai-stack.sh deploy dashboard     # same as restart for dashboard
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

CLI **`./ai-stack/ai-stack.sh stop`** (no service) does **not** use `bulk_ecosystem`; it stops **every** service. Per-service and core-infra commands are in **section 4.4**.

---

## 6. Deploying dashboard changes

After editing Python, templates, or static assets under **`dashboard/`**:

```bash
./ai-stack/services/dashboard.sh deploy
# or
./ai-stack/ai-stack.sh restart dashboard
```

The Dockerfile always rebuilds the image on **`start`/`deploy`**.

---

## 7. Traefik and routing changes

1. Edit **`traefik/dynamic.yml`** (keep **`ai-stack/config/dynamic.yml`** in sync if your workflow uses both).
2. Reload: restart Traefik or rely on file provider polling (Traefik v3 file provider watches the file).

```bash
./ai-stack/ai-stack.sh restart traefik
```

---

## 8. Cloudflare-local deployment

```bash
./ai-stack/ai-stack.sh start cloudflare-local
./ai-stack/ai-stack.sh logs cloudflare-local
```

Single-service recreate (example):

```bash
./ai-stack/services/cloudflare-local.sh recreate r2-adapter
```

Full compose rebuild:

```bash
docker compose -f cloudflare-local/docker-compose.yml up -d --build
```

---

## 9. Network repair

If you see **502 Bad Gateway** or containers not reachable by Traefik host rules:

```bash
./ai-stack/ai-stack.sh repair-network
```

This ensures **`lh-network`** exists, connects known containers (see **`NETWORK_CONTAINERS`** in **`core.sh`**), and applies **`docker update --restart unless-stopped`** on existing containers.

---

## 10. Ollama model pulls

```bash
./ai-stack/ai-stack.sh ollama-pull-models
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
| 502 from Traefik | Target container on **`lh-network`**; **`repair-network`**; service listening port matches **`dynamic.yml`** |
| Dashboard empty metrics | Docker socket mounted; time for samples to accumulate |
| macOS CPU temp flat / missing | **`~/.local-eco-host-metrics/cpu_temp_c.txt`**; LaunchAgent status; **`powermetrics`** sudo / NOPASSWD |
| n8n cookie / HTTPS issues | Env in **`n8n.sh`** (`N8N_TRUST_PROXY`, `N8N_SECURE_COOKIE`) |
| Java / mkcert | **`unset JAVA_HOME`** and retry **`mkcert -install`** |

**Logs:**

```bash
./ai-stack/ai-stack.sh logs n8n
./ai-stack/ai-stack.sh logs traefik
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
| [../README.md](../README.md) | Overview and quick links |
