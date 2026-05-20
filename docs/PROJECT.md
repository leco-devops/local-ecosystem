# LEco DevOps Open Project — repository guide

> **Open source** · [MIT License](../LICENSE) · Maintained by **[Techtonic Systems Media and Research LLC](https://techtonic.systems/)**  
> Landing page: [README](../README.md) · Stewardship: [OPEN_SOURCE.md](OPEN_SOURCE.md)

A **Docker-based open-source local platform** that mimics a small cloud edge: Traefik on `*.lh`, TLS, Ollama, Open WebUI, n8n, PostgreSQL, **LEco DevOps** (ops UI + app lifecycle tooling), and optional **Cloudflare-local** adapters (R2/KV/D1/Workers-style APIs).

| Layer | Role |
|--------|------|
| **DNS** (`*.lh`) | Resolve local hostnames to `127.0.0.1` (e.g. dnsmasq) |
| **Traefik** | HTTP/HTTPS entrypoints **80 / 443**, dashboard API **8080** |
| **mkcert** | Trusted dev certificates in `certs/` |
| **Containers** | Attached to Docker network **`lh-network`** |

You access services by name (**`https://n8n.lh`**, **`https://ai.lh`**, …) instead of memorizing ports.

---

## Project name

**LEco DevOps** is the application name (CLI + web UI). **LEco DevOps Open Project** is the open-source repository and community project, stewarded by [Techtonic Systems](https://techtonic.systems/).

---

## Documentation

| Guide | Description |
|--------|-------------|
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | Architecture hub: system overview plus links to HLD, LLD, and LEco tooling docs |
| **[HLD.md](HLD.md)** | High-level design: runtime layers, key flows, and integration boundaries |
| **[LLD.md](LLD.md)** | Low-level design: module ownership, API surface, and execution sequences |
| **[LECO_TOOLING.md](LECO_TOOLING.md)** | LEco toolchain map: CLI, manifests, registry, and dashboard interaction |
| **[SETUP.md](SETUP.md)** | **Complete first-time setup** — DNS, Docker, TLS, stack start, macOS host CPU metrics, optional Cloudflare-local |
| **[DEPLOYMENT.md](DEPLOYMENT.md)** | **Deployment and operations** — start/stop, updates, bulk vs Control API behavior, troubleshooting |
| **[RELEASE_NOTES.md](RELEASE_NOTES.md)** | **Release notes** — current version, history, upgrade notes |
| **[CHANGELOG.md](../CHANGELOG.md)** | Full changelog ([Keep a Changelog](https://keepachangelog.com/)) |
| **[VERSIONING.md](VERSIONING.md)** | Versioning policy, `VERSION` / `version.json`, release workflow |
| **[DEVELOPMENT_PLAYBOOK.md](DEVELOPMENT_PLAYBOOK.md)** | Extending services, LEco DevOps APIs, Traefik routes |
| **[LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md)** | LEco apps: bridge vs profile (v3), hosting symlinks, compose extras, teardown semantics |
| **[HOSTED_APPS_TRAEFIK_RUNBOOK.md](HOSTED_APPS_TRAEFIK_RUNBOOK.md)** | Hosted apps behind Traefik: 502, `lh-network`, DNS names, dashboard probes, same-origin `/api`, local edge runtimes |
| **[DEV_STACK_ISOLATION.md](DEV_STACK_ISOLATION.md)** | Isolated dev stacks, lifecycle, `platform.devStackId` |
| **[CLOUD_VM_DEPLOYMENT.md](CLOUD_VM_DEPLOYMENT.md)** | Cloud VM profiles, Platform tab, external LLM |
| **[help/03-platform-tab.md](help/03-platform-tab.md)** | User manual: Platform tab & dev stacks (also in dashboard **Help**) |
| **[cloudflare-local/README.md](../cloudflare-local/README.md)** | CF-local stack entry + links to architecture and user manual |
| **[CF_LECO_SERVICE_MAP.md](CF_LECO_SERVICE_MAP.md)** | Cloudflare ↔ LEco binding coverage, reuse rules, and adapter roadmap |

---

## Quick start (after prerequisites)

Prerequisites: **Docker**, **dnsmasq** (or equivalent) for **`*.lh`**, **mkcert** and certs for `*.lh`. Full steps are in **[SETUP.md](SETUP.md)**.

```bash
export REPO="$HOME/path/to/local-ecosystem"
cd "$REPO"

# Foundation installer (checks deps + asks each service)
./ecosystem-stack/install-foundation.sh

# Interactive menu
./ecosystem-stack/ecosystem-stack.sh menu

# Or start everything in dependency order
./ecosystem-stack/ecosystem-stack.sh start
```

**Default start order** (`ecosystem-stack/core.sh`): `traefik` → `postgres` → `ollama` → `webui` → `n8n` → `dashboard` → `cloudflare-local`.

Repair routing and network attachments anytime:

```bash
./ecosystem-stack/ecosystem-stack.sh repair-network
```

---

## Common URLs

| URL | Service |
|-----|---------|
| http://localhost.lh | LEco DevOps (via Traefik) |
| http://dashboard.lh | LEco DevOps (same app; add `dashboard.lh` to `*.lh` DNS like other `.lh` hosts) |
| http://localhost:8090 | LEco DevOps (direct host port; override with `DASHBOARD_HOST_PORT`) |
| https://traefik.lh | Traefik routing (TLS) |
| https://ai.lh | Open WebUI |
| https://n8n.lh | n8n |
| https://ollama.lh | Ollama |
| https://airllm.lh | AirLLM (large HuggingFace models) |
| http://r2.lh , http://kv.lh , http://d1.lh , http://workers.lh | Cloudflare-local (when started) |
| http://minio-console.lh , http://autoscale.lh | CF-local related UIs |

LEco DevOps provides **overview**, **metrics** (with optional host CPU temperature on macOS), **logs**, **docs**, **Control** (stack actions), and **Ollama** model management. Containers started via `ecosystem-stack` use **`--restart unless-stopped`** where applicable so they come back after a Docker daemon restart unless you stopped them explicitly.

---

## CLI reference (repository root)

```bash
./ecosystem-stack/ecosystem-stack.sh menu
./ecosystem-stack/ecosystem-stack.sh start [service]
./ecosystem-stack/ecosystem-stack.sh stop [service]
./ecosystem-stack/ecosystem-stack.sh restart [service]
./ecosystem-stack/ecosystem-stack.sh pause [service]
./ecosystem-stack/ecosystem-stack.sh unpause [service]
./ecosystem-stack/ecosystem-stack.sh status [service]
./ecosystem-stack/ecosystem-stack.sh logs [service]
./ecosystem-stack/ecosystem-stack.sh remove [service]
./ecosystem-stack/ecosystem-stack.sh reset [service]    # destructive — confirm prompts
./ecosystem-stack/ecosystem-stack.sh repair-network
./ecosystem-stack/ecosystem-stack.sh ollama-pull-models
```

Per-service scripts (same actions as each `*.sh` defines):

```bash
./ecosystem-stack/services/dashboard.sh deploy
./ecosystem-stack/services/cloudflare-local.sh start
```

---

## Repository layout (high level)

```
local-ecosystem/
├── ecosystem-stack/
│   ├── ecosystem-stack.sh   # CLI entry
│   ├── core.sh              # start order, network repair, bulk_ecosystem
│   ├── services/*.sh        # traefik, postgres, ollama, webui, n8n, dashboard, cloudflare-local
│   ├── scripts/             # macOS host CPU temp writer + LaunchAgent installer
│   └── config/              # e.g. ollama-pinned-models.txt, dynamic.yml copy
├── dashboard/               # LEco DevOps Flask app (image local/service-dashboard)
├── traefik/
├── hosting/traefik/         # runtime dynamic dir
├── cloudflare-local/        # docker-compose + adapters
├── certs/                   # mkcert *.lh PEMs
└── docs/                    # SETUP.md, DEPLOYMENT.md, DEVELOPMENT_PLAYBOOK.md
```

---

## macOS: host CPU temperature and LaunchAgent

The LEco DevOps container cannot read Apple SMC. On **macOS**, **`dashboard.sh`** mounts **`~/.local-eco-host-metrics`** and sets **`DASHBOARD_HOST_CPU_TEMP_FILE`**.

When you **deploy/start** LEco DevOps via **`ecosystem-stack/services/dashboard.sh`**, a **LaunchAgent** is installed to run **`ecosystem-stack/scripts/macos-write-cpu-temp.sh`** every **30s**. **Stop/remove** via the same script **uninstalls** that agent. Optional **`brew install osx-cpu-temp`**; Apple Silicon often needs **`sudo powermetrics`** (see script and **[SETUP.md](SETUP.md)** §8).

---

## Ollama pinned models

Edit **`ecosystem-stack/config/ollama-pinned-models.txt`** (one model per line). On Ollama service start, pulls run in the background. To refresh a running container:

```bash
./ecosystem-stack/ecosystem-stack.sh ollama-pull-models
```

## AirLLM (large HuggingFace models)

[AirLLM](https://github.com/lyogavin/airllm) enables 70B/405B inference on modest hardware by streaming model layers from disk. It runs as a dedicated `airllm` Docker container on `lh-network` and exposes an **Ollama-compatible** API.

- **URL**: `https://airllm.lh` (Traefik) or `http://airllm:11435` (intra-network)
- **Pinned models**: `ecosystem-stack/config/airllm-pinned-models.txt`
- **CLI**: `./ecosystem-stack/ecosystem-stack.sh airllm-pull-models` or `./leco-cli.sh airllm pull`

See **[AIRLLM_INTEGRATION.md](AIRLLM_INTEGRATION.md)** for full details.

---

## Troubleshooting (short)

| Issue | Action |
|-------|--------|
| `*.lh` does not resolve | dnsmasq + `/etc/resolver/lh` — see [SETUP.md](SETUP.md) §4 |
| Bad Gateway | `./ecosystem-stack/ecosystem-stack.sh repair-network`; Hosted apps: **[HOSTED_APPS_TRAEFIK_RUNBOOK.md](HOSTED_APPS_TRAEFIK_RUNBOOK.md)** |
| TLS warnings | mkcert CA + files in `certs/` |
| n8n cookies / HTTPS | Env in `ecosystem-stack/services/n8n.sh` (`N8N_TRUST_PROXY`, `N8N_SECURE_COOKIE`) |

More: **[DEPLOYMENT.md](DEPLOYMENT.md)** §12.

---

## License and contributing

This project is **open source** under the **[MIT License](../LICENSE)**.

**Copyright (c) Techtonic Systems Media and Research LLC** and contributors. See **[NOTICE.md](../NOTICE.md)** and **[OPEN_SOURCE.md](OPEN_SOURCE.md)**.

To contribute, open an issue or pull request and follow **[CONTRIBUTING.md](../CONTRIBUTING.md)** and **[DEVELOPMENT_PLAYBOOK.md](DEVELOPMENT_PLAYBOOK.md)**. Control actions can be destructive; use **`DASHBOARD_CONTROL_TOKEN`** in production-like environments.
