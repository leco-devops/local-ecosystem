# Local Ecosystem — AI stack and local platform

A **Docker-based local platform** that mimics a small cloud edge: Traefik on `*.lh`, TLS, Ollama, Open WebUI, n8n, PostgreSQL, an ops **dashboard**, and optional **Cloudflare-local** adapters (R2/KV/D1/Workers-style APIs).

| Layer | Role |
|--------|------|
| **DNS** (`*.lh`) | Resolve local hostnames to `127.0.0.1` (e.g. dnsmasq) |
| **Traefik** | HTTP/HTTPS entrypoints **80 / 443**, dashboard API **8080** |
| **mkcert** | Trusted dev certificates in `certs/` |
| **Containers** | Attached to Docker network **`lh-network`** |

You access services by name (**`https://n8n.lh`**, **`https://ai.lh`**, …) instead of memorizing ports.

---

## Documentation

| Guide | Description |
|--------|-------------|
| **[docs/SETUP.md](docs/SETUP.md)** | **Complete first-time setup** — DNS, Docker, TLS, stack start, macOS host CPU metrics, optional Cloudflare-local |
| **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** | **Deployment and operations** — start/stop, updates, bulk vs Control API behavior, troubleshooting |
| **[docs/DEVELOPMENT_PLAYBOOK.md](docs/DEVELOPMENT_PLAYBOOK.md)** | Extending services, dashboard APIs, Traefik routes |
| **[docs/LECO_APP_BLUEPRINT.md](docs/LECO_APP_BLUEPRINT.md)** | LEco apps: bridge vs profile (v3), hosting symlinks, compose extras, teardown semantics |
| **[cloudflare-local/README.md](cloudflare-local/README.md)** | CF-local stack entry + links to architecture and user manual |

---

## Quick start (after prerequisites)

Prerequisites: **Docker**, **dnsmasq** (or equivalent) for **`*.lh`**, **mkcert** and certs for `*.lh`. Full steps are in **[docs/SETUP.md](docs/SETUP.md)**.

```bash
export REPO="$HOME/path/to/local-ecosystem"
cd "$REPO"

# Interactive menu
./ai-stack/ai-stack.sh menu

# Or start everything in dependency order
./ai-stack/ai-stack.sh start
```

**Default start order** (`ai-stack/core.sh`): `traefik` → `postgres` → `ollama` → `webui` → `n8n` → `dashboard` → `cloudflare-local`.

Repair routing and network attachments anytime:

```bash
./ai-stack/ai-stack.sh repair-network
```

---

## Common URLs

| URL | Service |
|-----|---------|
| http://localhost.lh | Ops dashboard (via Traefik) |
| http://localhost:8090 | Ops dashboard (direct host port; override with `DASHBOARD_HOST_PORT`) |
| https://traefik.lh | Traefik routing (TLS) |
| https://ai.lh | Open WebUI |
| https://n8n.lh | n8n |
| https://ollama.lh | Ollama |
| http://r2.lh , http://kv.lh , http://d1.lh , http://workers.lh | Cloudflare-local (when started) |
| http://minio-console.lh , http://autoscale.lh | CF-local related UIs |

The dashboard provides **overview**, **metrics** (with optional host CPU temperature on macOS), **logs**, **docs**, **Control** (stack actions), and **Ollama** model management. Containers started via `ai-stack` use **`--restart unless-stopped`** where applicable so they come back after a Docker daemon restart unless you stopped them explicitly.

---

## CLI reference (repository root)

```bash
./ai-stack/ai-stack.sh menu
./ai-stack/ai-stack.sh start [service]
./ai-stack/ai-stack.sh stop [service]
./ai-stack/ai-stack.sh restart [service]
./ai-stack/ai-stack.sh pause [service]
./ai-stack/ai-stack.sh unpause [service]
./ai-stack/ai-stack.sh status [service]
./ai-stack/ai-stack.sh logs [service]
./ai-stack/ai-stack.sh remove [service]
./ai-stack/ai-stack.sh reset [service]    # destructive — confirm prompts
./ai-stack/ai-stack.sh repair-network
./ai-stack/ai-stack.sh ollama-pull-models
```

Per-service scripts (same actions as each `*.sh` defines):

```bash
./ai-stack/services/dashboard.sh deploy
./ai-stack/services/cloudflare-local.sh start
```

---

## Repository layout (high level)

```
local-ecosystem/
├── ai-stack/
│   ├── ai-stack.sh          # CLI entry
│   ├── core.sh              # start order, network repair, bulk_ecosystem
│   ├── services/*.sh        # traefik, postgres, ollama, webui, n8n, dashboard, cloudflare-local
│   ├── scripts/             # macOS host CPU temp writer + LaunchAgent installer
│   └── config/              # e.g. ollama-pinned-models.txt, dynamic.yml copy
├── dashboard/               # Flask ops app (image local/service-dashboard)
├── traefik/
│   └── dynamic.yml          # *.lh routers (keep in sync with ai-stack/config if you use both)
├── cloudflare-local/        # docker-compose + adapters
├── certs/                   # mkcert *.lh PEMs
└── docs/                    # SETUP.md, DEPLOYMENT.md, DEVELOPMENT_PLAYBOOK.md
```

---

## macOS: host CPU temperature and LaunchAgent

The dashboard container cannot read Apple SMC. On **macOS**, **`dashboard.sh`** mounts **`~/.local-eco-host-metrics`** and sets **`DASHBOARD_HOST_CPU_TEMP_FILE`**.

When you **deploy/start** the dashboard via **`ai-stack/services/dashboard.sh`**, a **LaunchAgent** is installed to run **`ai-stack/scripts/macos-write-cpu-temp.sh`** every **30s**. **Stop/remove** via the same script **uninstalls** that agent. Optional **`brew install osx-cpu-temp`**; Apple Silicon often needs **`sudo powermetrics`** (see script and **[docs/SETUP.md](docs/SETUP.md)** §8).

---

## Ollama pinned models

Edit **`ai-stack/config/ollama-pinned-models.txt`** (one model per line). On Ollama service start, pulls run in the background. To refresh a running container:

```bash
./ai-stack/ai-stack.sh ollama-pull-models
```

---

## Troubleshooting (short)

| Issue | Action |
|-------|--------|
| `*.lh` does not resolve | dnsmasq + `/etc/resolver/lh` — see [docs/SETUP.md](docs/SETUP.md) §4 |
| Bad Gateway | `./ai-stack/ai-stack.sh repair-network` |
| TLS warnings | mkcert CA + files in `certs/` |
| n8n cookies / HTTPS | Env in `ai-stack/services/n8n.sh` (`N8N_TRUST_PROXY`, `N8N_SECURE_COOKIE`) |

More: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** §12.

---

## License / contributing

Follow **[docs/DEVELOPMENT_PLAYBOOK.md](docs/DEVELOPMENT_PLAYBOOK.md)** when changing services, Traefik, or the dashboard. Control actions can be destructive; use **`DASHBOARD_CONTROL_TOKEN`** in production-like environments.
