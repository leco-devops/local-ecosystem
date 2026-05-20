# Requirements & prerequisites

## Host machine

| Requirement | Notes |
|-------------|--------|
| **Docker Desktop** (macOS/Windows) or **Docker Engine** (Linux) | Compose v2 (`docker compose`) required |
| **Python 3.11+** | For `leco-devops` CLI and dashboard development |
| **~25 GB free disk** | Minimum for stack + one medium Ollama model; AirLLM large models need much more |
| **8 GB RAM** | Comfortable minimum; 16 GB+ recommended if running WebUI + n8n + a 7B model |
| **Admin / sudo** (optional) | Only for `*.lh` DNS on macOS (`/etc/hosts`) or `powermetrics` temperature on Apple Silicon |

## Network

- **`*.lh` hostnames** resolve to `127.0.0.1` (see [DNS setup](help:install-dns)).
- Traefik listens on **80** and **443** on the host — ensure nothing else binds those ports.
- All stack containers join Docker network **`lh-network`**.

## Storage (Docker Desktop on macOS)

Point Docker Desktop **Disk image location** to external NVMe if you do not want large model downloads on the internal SSD:

**Docker Desktop → Settings → Resources → Advanced → Disk image location**

Ollama blobs, AirLLM HF cache, and image layers all live in that single disk image.

## What is *not* required on the host

- A host install of **Ollama** (runs in container `ollama`)
- A host install of **AirLLM** / Python venv (runs in container `airllm`)
- **CUDA** on macOS (AirLLM uses CPU inside Docker; GPU optional on Linux)

## Optional tools

| Tool | Purpose |
|------|---------|
| `leco-cli.sh` | Menu wrapper around `ecosystem-stack.sh` |
| `wrangler` | Cloudflare Workers deploy from `leco-devops cf-deploy` |
| `gh` | GitHub PR/issue workflows (not required for stack) |

## Repository layout (clone once)

```text
local-ecosystem/
  ecosystem-stack/     # start/stop services
  dashboard/           # LEco DevOps UI (built into service-dashboard image)
  tools/deploy-cli/    # leco-devops CLI
  traefik/             # canonical routing YAML
  hosting/             # app-available + traefik merge target
  config/              # leco-registry.yaml
  docs/help/           # this manual
```

Next: [Installation → Ecosystem stack](help:install-stack)
