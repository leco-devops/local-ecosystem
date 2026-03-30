# Deployment and operations — Local Ecosystem

This guide covers **starting, updating, stopping, and debugging** the stack after initial setup. For first-time installation, see **[SETUP.md](SETUP.md)**.

---

## 1. Entry points

| Tool | Path | Use when |
|------|------|----------|
| Interactive menu | `./ai-stack/ai-stack.sh menu` | Exploring services |
| CLI | `./ai-stack/ai-stack.sh <action> [service]` | Scripts and CI |
| Per-service script | `./ai-stack/services/<name>.sh <action>` | Direct control of one unit |
| Dashboard **Control** tab | http://localhost.lh | Same actions with optional token |

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

The **`bulk_ecosystem`** helper in **`core.sh`** (used when the dashboard **Control** tab runs **`stack-ecosystem-all`** with **`stop` / `remove` / `reset` / `pause`**) walks services in **reverse start order** but **skips `dashboard`** so the container handling the HTTP request is not stopped mid-flight. Starting or restarting the full stack from Control still starts every service, including the dashboard.

To stop only the dashboard from the shell:

```bash
./ai-stack/ai-stack.sh stop dashboard
```

### 4.4 Pause / unpause

```bash
./ai-stack/ai-stack.sh pause
./ai-stack/ai-stack.sh unpause
```

**CLI** `pause` / `unpause` with **no service name** walks **all** `services/*.sh` (like **`stop`**), **including the dashboard**. The dashboard **Control** bulk **`pause`** path uses **`bulk_ecosystem`** and **skips the dashboard** (same idea as bulk **stop**).

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
| `stop` | Stop all in reverse `START_ORDER`, **skipping dashboard** |
| `restart` / `deploy` | Same stop phase (skip dashboard), then start all |
| `remove` / `reset` | Tear down in reverse order, **skipping dashboard** |
| `recreate` | Remove phase (skip dashboard), then start all |

CLI **`./ai-stack/ai-stack.sh stop`** does **not** use `bulk_ecosystem`; it stops **every** service, including the dashboard.

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
