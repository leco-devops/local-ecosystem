# LEco DevOps Open Project

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

**LEco DevOps** is the application name (CLI + web UI). **LEco DevOps Open Project** is the open-source repository and community project.

---

## Documentation

| Guide | Description |
|--------|-------------|
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | Architecture hub: system overview plus links to HLD, LLD, and LEco tooling docs |
| **[docs/HLD.md](docs/HLD.md)** | High-level design: runtime layers, key flows, and integration boundaries |
| **[docs/LLD.md](docs/LLD.md)** | Low-level design: module ownership, API surface, and execution sequences |
| **[docs/LECO_TOOLING.md](docs/LECO_TOOLING.md)** | LEco toolchain map: CLI, manifests, registry, and dashboard interaction |
| **[docs/SETUP.md](docs/SETUP.md)** | **Complete first-time setup** — DNS, Docker, TLS, stack start, macOS host CPU metrics, optional Cloudflare-local |
| **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** | **Deployment and operations** — start/stop, updates, bulk vs Control API behavior, troubleshooting |
| **[docs/DEVELOPMENT_PLAYBOOK.md](docs/DEVELOPMENT_PLAYBOOK.md)** | Extending services, LEco DevOps APIs, Traefik routes |
| **[docs/LECO_APP_BLUEPRINT.md](docs/LECO_APP_BLUEPRINT.md)** | LEco apps: bridge vs profile (v3), hosting symlinks, compose extras, teardown semantics |
| **[docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md](docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md)** | Hosted apps behind Traefik: 502, `lh-network`, DNS names, dashboard probes, same-origin `/api`, local edge runtimes (Workers / Pages / Vercel / Lambda / Deno) |
| **[cloudflare-local/README.md](cloudflare-local/README.md)** | CF-local stack entry + links to architecture and user manual |

---

## Quick start (after prerequisites)

Prerequisites: **Docker**, **dnsmasq** (or equivalent) for **`*.lh`**, **mkcert** and certs for `*.lh`. Full steps are in **[docs/SETUP.md](docs/SETUP.md)**.

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
│   ├── traefik-static.yaml  # entrypoints, file provider → hosting/traefik/
│   └── dynamic.yml          # canonical *.lh stack routes (copied to hosting/traefik/01-stack-core.yml on Traefik start)
├── hosting/traefik/         # runtime dynamic dir (gitignored: dynamic.yml merge fragment; 01-stack-core.yml copy)
├── cloudflare-local/        # docker-compose + adapters
├── certs/                   # mkcert *.lh PEMs
└── docs/                    # SETUP.md, DEPLOYMENT.md, DEVELOPMENT_PLAYBOOK.md
```

---

## macOS: host CPU temperature and LaunchAgent

The LEco DevOps container cannot read Apple SMC. On **macOS**, **`dashboard.sh`** mounts **`~/.local-eco-host-metrics`** and sets **`DASHBOARD_HOST_CPU_TEMP_FILE`**.

When you **deploy/start** LEco DevOps via **`ecosystem-stack/services/dashboard.sh`**, a **LaunchAgent** is installed to run **`ecosystem-stack/scripts/macos-write-cpu-temp.sh`** every **30s**. **Stop/remove** via the same script **uninstalls** that agent. Optional **`brew install osx-cpu-temp`**; Apple Silicon often needs **`sudo powermetrics`** (see script and **[docs/SETUP.md](docs/SETUP.md)** §8).

---

## Ollama pinned models

Edit **`ecosystem-stack/config/ollama-pinned-models.txt`** (one model per line). On Ollama service start, pulls run in the background. To refresh a running container:

```bash
./ecosystem-stack/ecosystem-stack.sh ollama-pull-models
```

## AirLLM (large HuggingFace models)

[AirLLM](https://github.com/lyogavin/airllm) enables 70B/405B inference on modest hardware by streaming model layers from disk. It runs as a dedicated `airllm` Docker container on `lh-network` and exposes an **Ollama-compatible** API (`/api/tags`, `/api/pull`, `/api/generate`, `/api/chat`, …) so the existing dashboard, AI provider abstraction and Ollama-compatible clients all work without special-casing.

- **URL**: `https://airllm.lh` (Traefik) or `http://airllm:11435` (intra-network) or `http://127.0.0.1:11435` (host)
- **Pinned models**: `ecosystem-stack/config/airllm-pinned-models.txt` (HuggingFace ids, one per line)
- **CLI**: `./ecosystem-stack/ecosystem-stack.sh airllm-pull-models` or `./leco-cli.sh airllm pull`
- **Build**: `./leco-cli.sh airllm build` (or `AIRLLM_FORCE_BUILD=1` to rebuild after editing the shim)
- **Dashboard**: Infrastructure → 6 · AirLLM (Large Models)

AirLLM uses HuggingFace `safetensors`, not Ollama's GGUF, so its model registry is separate from Ollama. On macOS the container is CPU-only (Docker Desktop's Linux VM can't see Apple Silicon GPU); on Linux+CUDA hosts you can rebuild with a CUDA torch wheel and add `--gpus=all`.

See **[docs/AIRLLM_INTEGRATION.md](docs/AIRLLM_INTEGRATION.md)** for full details.

---

## Troubleshooting (short)

| Issue | Action |
|-------|--------|
| `*.lh` does not resolve | dnsmasq + `/etc/resolver/lh` — see [docs/SETUP.md](docs/SETUP.md) §4 |
| Bad Gateway | `./ecosystem-stack/ecosystem-stack.sh repair-network`; Hosted apps: **[docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md](docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md)** |
| TLS warnings | mkcert CA + files in `certs/` |
| n8n cookies / HTTPS | Env in `ecosystem-stack/services/n8n.sh` (`N8N_TRUST_PROXY`, `N8N_SECURE_COOKIE`) |

More: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** §12.

---

## License / contributing

This project is released under the **MIT License**. See **[LICENSE](LICENSE)**.

Maintainer: **Rajneesh Maurya** (individual open-source steward).

To contribute, open an issue or pull request and follow **[CONTRIBUTING.md](CONTRIBUTING.md)** and **[docs/DEVELOPMENT_PLAYBOOK.md](docs/DEVELOPMENT_PLAYBOOK.md)**. Control actions can be destructive; use **`DASHBOARD_CONTROL_TOKEN`** in production-like environments.

## Important links

- **Project docs:** [docs/SETUP.md](docs/SETUP.md), [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/DEVOPS_GUIDE.md](docs/DEVOPS_GUIDE.md)
- **Architecture docs:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/HLD.md](docs/HLD.md), [docs/LLD.md](docs/LLD.md)
- **LEco DevOps app tooling:** [docs/LECO_TOOLING.md](docs/LECO_TOOLING.md), [docs/DEPLOY_CLI.md](docs/DEPLOY_CLI.md), [docs/LECO_USER_MANUAL.md](docs/LECO_USER_MANUAL.md), [docs/LECO_APP_BLUEPRINT.md](docs/LECO_APP_BLUEPRINT.md), [docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md](docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md)
- **Open-source docs:** [LICENSE](LICENSE), [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md)
- **Automation guide:** [AGENTS.md](AGENTS.md)
