# AirLLM Integration

[AirLLM](https://github.com/lyogavin/airllm) is a Python library that enables large language model (70B/405B) inference on limited GPU VRAM by loading models layer-by-layer. This document describes how AirLLM is integrated into the LEco DevOps local ecosystem as a second local LLM backend, complementing Ollama.

In LEco DevOps, AirLLM is wrapped in a small FastAPI shim that speaks the **Ollama wire protocol** (`/api/tags`, `/api/ps`, `/api/pull`, `/api/generate`, `/api/chat`, …) so the existing dashboard, AI provider abstraction, and any Ollama-compatible client (Open WebUI, etc.) can use it with zero special casing.

## Overview

| Aspect | Ollama | AirLLM |
|--------|--------|--------|
| Model format | GGUF (Ollama registry) | HuggingFace `safetensors` |
| Sweet spot | Small/medium models, fast inference | Huge models (70B/405B), limited RAM/VRAM |
| Runtime | Go server in `ollama` container | Python FastAPI shim in `airllm` container |
| GPU support | CUDA/Metal via container | CPU by default; CUDA on Linux+`--gpus=all` |
| Storage | `ollama` Docker volume | `airllm_hf_cache` + `airllm_layer_shards` Docker volumes |
| API | Ollama REST API | Ollama-compatible shim at `airllm.lh` |
| Network | `airllm:11435` (intra-network) and `127.0.0.1:11435` (host port) | |

## Architecture

```
                 ┌──────────────┐
Browser/WebUI ──▶│  Traefik     │── ai.lh / ollama.lh ──▶ ollama       (GGUF)
                 │  *.lh        │── airllm.lh ─────────▶ airllm        (HF safetensors)
                 └──────────────┘
                                                              ▲
LEco Dashboard (service-dashboard) ──── /api/ollama/* ───────┤
                                  ──── /api/airllm/* ────────┘
                                       (resolves to `airllm:11435`
                                        on the shared lh-network)
```

Both runtimes sit on the same `lh-network` Docker network and are reachable from any other ecosystem container by name (`http://ollama:11434`, `http://airllm:11435`). Outside containers, use the published host ports (`127.0.0.1:11434`, `127.0.0.1:11435`) or the Traefik hostnames (`https://ollama.lh`, `https://airllm.lh`).

## Why a container (and not a host service)?

Earlier drafts of this integration ran the shim as a macOS LaunchAgent on the host so it could use Apple's MLX framework for GPU acceleration. That tradeoff was reverted in favour of **Docker-only deployment** because:

- Same install/upgrade/control surface as every other stack service (`./ecosystem-stack/services/airllm.sh {start|stop|restart|...}`, dashboard Control tab, `leco-cli.sh airllm …`) — no host venvs, no `launchd`, no platform forks.
- One Dockerfile, one image build, one set of volumes for every developer.
- The shim's value is **routing huge models off VRAM and onto disk/RAM**, not MLX-grade tensor performance. CPU throughput is the bottleneck either way once a 70B model is layer-streaming.

**Trade-off**: on macOS the container runs in the Docker Desktop Linux VM, so Apple Silicon GPU acceleration is *not* available. Inference happens on CPU (via PyTorch). For GPU acceleration, run the same image on a Linux host with `--gpus=all` and a CUDA build of `torch` (override via image rebuild or `extra-index-url`).

## Files

| Path | Purpose |
|------|---------|
| `ecosystem-stack/airllm/Dockerfile` | Build recipe (python:3.11-slim + torch CPU + airllm + FastAPI) |
| `ecosystem-stack/airllm/requirements.txt` | Pinned Python deps (no MLX) |
| `ecosystem-stack/airllm/server.py` | The FastAPI Ollama-compatible shim |
| `ecosystem-stack/services/airllm.sh` | `start/stop/restart/status/logs/remove/reset/pull-models/build` |
| `ecosystem-stack/config/airllm-pinned-models.txt` | One HF model id per line; pulled on `start` |
| `dashboard/airllm_models.py` | Dashboard backend talking to the shim's `/api/*` |
| `dashboard/ai_provider.py::AirLLMProvider` | `OllamaProvider` subclass pointing at `http://airllm:11435` |

## Installation

There is no separate install step — it is a normal stack service:

```bash
./ecosystem-stack/ecosystem-stack.sh start airllm
# or via leco-cli:
./leco-cli.sh airllm start
```

On first start the service script will:

1. Ensure the `lh-network` Docker network exists.
2. Build `local-airllm:latest` from `ecosystem-stack/airllm/Dockerfile` if absent (first build downloads torch + airllm, ~2GB).
3. Create the `airllm_hf_cache` and `airllm_layer_shards` Docker volumes if absent.
4. Run the `airllm` container on `lh-network`, publishing `${AIRLLM_PORT_HOST:-11435}` → `${AIRLLM_PORT:-11435}`.
5. Pull pinned models from `ecosystem-stack/config/airllm-pinned-models.txt` via the shim's `/api/pull`.

To force a rebuild after editing `server.py` or `requirements.txt`:

```bash
AIRLLM_FORCE_BUILD=1 ./leco-cli.sh airllm build
./leco-cli.sh airllm restart
```

## Configuration

### Environment variables read by `airllm.sh`

| Variable | Default | Description |
|----------|---------|-------------|
| `AIRLLM_PORT` | `11435` | In-container shim port (sets `EXPOSE`) |
| `AIRLLM_PORT_HOST` | same as `AIRLLM_PORT` | Host-side published port |
| `AIRLLM_COMPRESSION` | `none` | `none` / `4bit` / `8bit` (passed to AirLLM) |
| `AIRLLM_KEEP_ALIVE` | `300` | Seconds the shim keeps an idle model loaded |
| `AIRLLM_READY_TIMEOUT` | `120` | How long `pull_pinned_models` waits for the shim to come up |
| `AIRLLM_FORCE_BUILD` | unset | Rebuild image even if it already exists |
| `HF_TOKEN` | unset | HuggingFace token for gated models |

### Environment variables read by the shim (inside the container)

| Variable | Default | Description |
|----------|---------|-------------|
| `AIRLLM_PORT` | `11435` | Listen port |
| `AIRLLM_HF_HOME` | `/data/hf-cache` | HuggingFace cache (mounted from `airllm_hf_cache` volume) |
| `AIRLLM_LAYER_SHARDS_DIR` | `/data/shards` | AirLLM shard storage (mounted from `airllm_layer_shards` volume) |
| `AIRLLM_COMPRESSION` | `none` | Default quantization |
| `HF_TOKEN` | unset | HuggingFace token |
| `AIRLLM_KEEP_ALIVE` | `300` | Idle keep-alive seconds |

### Pinned models

Edit `ecosystem-stack/config/airllm-pinned-models.txt`:

```text
# Small (handy for smoke tests; ~1-2 GB each)
Qwen/Qwen2.5-0.5B-Instruct
Qwen/Qwen2.5-1.5B-Instruct

# Medium daily-driver (7-8 GB each)
Qwen/Qwen2.5-7B-Instruct
meta-llama/Llama-3.2-8B-Instruct

# Large (AirLLM's specialty; tens to hundreds of GB)
Qwen/Qwen2.5-72B-Instruct
meta-llama/Meta-Llama-3.1-70B-Instruct   # requires HF_TOKEN
```

Pull pinned models on demand:

```bash
./ecosystem-stack/ecosystem-stack.sh airllm-pull-models
# or
./leco-cli.sh airllm pull
```

## Usage

### Dashboard

Open `https://localhost.lh` → **Infrastructure** → **6 · AirLLM (Large Models)**. Features:

- View cached HF repos and the currently loaded model (`/api/tags`, `/api/ps`).
- Pull by HuggingFace id (`/api/pull`).
- Pin/unpin entries in `airllm-pinned-models.txt`.
- Unload from RAM (`keep_alive=0`).
- Snapshot/restore the pinned list under `.local-eco-backups/`.

### API

The shim exposes a subset of the Ollama API at `https://airllm.lh` (Traefik) and `http://airllm:11435` (intra-network):

```bash
# list cached models
curl https://airllm.lh/api/tags

# pull
curl -X POST https://airllm.lh/api/pull \
    -H 'Content-Type: application/json' \
    -d '{"name": "Qwen/Qwen2.5-7B-Instruct"}'

# generate (NDJSON stream like Ollama)
curl -N -X POST https://airllm.lh/api/generate \
    -H 'Content-Type: application/json' \
    -d '{"model": "Qwen/Qwen2.5-7B-Instruct", "prompt": "Capital of France?"}'

# chat
curl -X POST https://airllm.lh/api/chat \
    -H 'Content-Type: application/json' \
    -d '{"model": "Qwen/Qwen2.5-7B-Instruct",
         "messages": [{"role": "user", "content": "Hello"}],
         "stream": false}'
```

### Open WebUI

Add a second OpenAI-compatible / Ollama endpoint pointing at `http://airllm:11435` (from another container on `lh-network`) or `http://host.docker.internal:11435` (from a host browser via the published port).

### AI-assisted onboarding

In **Infrastructure → 7 · AI-assisted onboarding**, pick **AirLLM (local large models)** as the provider, or pick it as the **local SLM** when **Hybrid** mode is selected.

## CLI commands

```bash
# Service lifecycle
./ecosystem-stack/ecosystem-stack.sh start|stop|restart airllm
./leco-cli.sh airllm start|stop|restart|status|logs

# Image build (first-time / rebuilds)
./leco-cli.sh airllm build                 # docker build ecosystem-stack/airllm
AIRLLM_FORCE_BUILD=1 ./leco-cli.sh airllm build   # rebuild even if image exists

# Pull pinned models
./leco-cli.sh airllm pull
./ecosystem-stack/ecosystem-stack.sh airllm-pull-models

# Quick API probe (uses docker exec from inside the container)
./leco-cli.sh airllm list

# Open the routed URL
./leco-cli.sh airllm open
./leco-cli.sh open airllm

# Teardown
./leco-cli.sh airllm remove                # remove container, keep HF cache + shards
./leco-cli.sh airllm reset                 # remove container AND delete both volumes
```

## Storage requirements

AirLLM downloads HuggingFace weights *and* writes per-layer shards. Two Docker volumes hold both:

| Volume | Mount inside container | Holds |
|--------|------------------------|-------|
| `airllm_hf_cache` | `/data/hf-cache` | Raw HF safetensors |
| `airllm_layer_shards` | `/data/shards` | AirLLM-split per-layer files |

Approximate sizes:

| Model | HF cache | AirLLM shards | Total |
|-------|----------|---------------|-------|
| Qwen2.5-0.5B | ~1 GB | ~0.5 GB | ~1.5 GB |
| Qwen2.5-7B | ~15 GB | ~7 GB | ~22 GB |
| Llama-3.1-70B | ~140 GB | ~70 GB | ~210 GB |
| Llama-3.1-405B | ~800 GB | ~400 GB | ~1.2 TB |

`./leco-cli.sh airllm reset` removes both volumes (force re-download on next start).

## GPU acceleration (optional, Linux+CUDA only)

Docker Desktop on macOS cannot expose the Apple GPU to a Linux container, so the default image runs CPU-only on macOS. On a Linux host with NVIDIA + nvidia-container-toolkit:

1. Rebuild with the **CUDA torch wheel index** via `--build-arg`:
   ```bash
   docker build \
       --build-arg AIRLLM_TORCH_INDEX=https://download.pytorch.org/whl/cu121 \
       -t local-airllm:latest ecosystem-stack/airllm
   ```
   The default in the Dockerfile is `https://download.pytorch.org/whl/cpu` so
   macOS / non-GPU hosts don't accidentally pull ~2GB of `nvidia_*` CUDA wheels.
2. Edit `ecosystem-stack/services/airllm.sh` `start()` to add `--gpus=all` to the `docker run` command (or set `AIRLLM_DOCKER_EXTRA_ARGS` if you prefer a future env-driven hook — see TODO).

## Troubleshooting

### No `airllm` container in Docker Desktop / `airllm.lh` returns 404 or 502

The stack does not create the container until you start the service. If you brought the rest of the stack up before AirLLM was added, or `./ecosystem-stack.sh start` stopped at an earlier failure, Docker Desktop will simply not list `airllm` until you start it:

```bash
./ecosystem-stack/ecosystem-stack.sh start airllm
# or
./leco-cli.sh airllm start
```

The first start builds `local-airllm:latest` (~2GB download); watch it with `./leco-cli.sh airllm build` if `start` seems to hang. Then confirm:

```bash
docker ps --filter name=airllm
curl -fsS http://127.0.0.1:11435/health
curl -fsS https://airllm.lh/health   # or http://airllm.lh/health
```

### Container doesn't come up

```bash
docker ps -a --filter name=airllm
docker logs airllm

# First build can take several minutes (torch ~2GB). Watch progress:
./leco-cli.sh airllm build
```

### `/api/tags` returns nothing

The shim creates `/data/hf-cache` empty on first start. Pull at least one pinned model:

```bash
./leco-cli.sh airllm pull
```

### Out of memory during inference

- Set `AIRLLM_COMPRESSION=4bit` in `airllm.sh` env and restart.
- Pick a smaller model.
- Unload the previously loaded model from the dashboard panel.

### Image rebuild after `server.py` change

```bash
AIRLLM_FORCE_BUILD=1 ./leco-cli.sh airllm build
./leco-cli.sh airllm restart
```

### Pulls hang on gated repos

Put your HF token in the host environment of the service script (or export `HF_TOKEN=...` before running `airllm start`) so it's threaded through to the container.

## Dependency pin notes (May 2026)

AirLLM 2.11 (latest on PyPI) was published against an older HuggingFace stack and breaks at import time on the modern releases. `ecosystem-stack/airllm/requirements.txt` pins the three transitive deps that matter:

| Pin | Reason |
|-----|--------|
| `airllm>=2.11.0` | Latest release on PyPI; higher floors fail with `No matching distribution`. |
| `optimum>=1.13,<1.18` | airllm imports `optimum.bettertransformer`, removed in optimum 1.18 in favour of PyTorch SDPA. |
| `transformers>=4.46,<4.49` | airllm + optimum<1.18 import `transformers.utils.is_tf_available`, removed in transformers 4.49 when TF support was dropped. |
| `torch>=2.5.0` from `https://download.pytorch.org/whl/cpu` (Dockerfile `ARG AIRLLM_TORCH_INDEX`) | Default PyPI metadata pulls ~2GB of `nvidia_cudnn_cu13` / `nccl` / `cusparselt` wheels on aarch64 too — useless inside Docker Desktop on macOS. CPU wheel is ~150 MB. |

Bump only when upstream airllm migrates to native PyTorch SDPA (`torch.nn.functional.scaled_dot_product_attention`) and drops the TF compatibility imports — verify by removing the pins, rebuilding, and checking that `from airllm import AutoModel` succeeds.

## Limitations

- **CPU-only on macOS by default**: no MLX/Metal acceleration in Docker Desktop's Linux VM (see [GPU acceleration](#gpu-acceleration-optional-linuxcuda-only) above).
- **One model loaded at a time**: AirLLM keeps one model in memory; loading another evicts the previous.
- **No model sharing with Ollama**: AirLLM consumes HuggingFace `safetensors`, not GGUF.
- **First load latency**: shard creation on first invocation can be several minutes for very large models.

## References

- [AirLLM on GitHub](https://github.com/lyogavin/airllm)
- [HuggingFace Hub](https://huggingface.co/models)
- [Ollama API Reference](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [`HOSTED_APPS_TRAEFIK_RUNBOOK.md`](HOSTED_APPS_TRAEFIK_RUNBOOK.md) — Traefik routing model used by `airllm.lh`
