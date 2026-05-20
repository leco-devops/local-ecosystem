# AirLLM (large HuggingFace models)

**Container:** `airllm` · **URL:** `https://airllm.lh` · **Shim API:** Ollama-compatible on port 11435 (CPU in Docker).

AirLLM loads **HuggingFace safetensors** layer-by-layer so very large models (32B–70B+) can run with limited RAM/VRAM.

## First-time build & start

```bash
./leco-cli.sh airllm build    # ~2–5 min first time (CPU torch, no CUDA wheels on macOS)
./leco-cli.sh airllm start
docker ps --filter name=airllm
curl -fsS https://airllm.lh/health
```

If build fails on `airllm>=2.12`, ensure you have the latest repo (pins `airllm 2.11` + `optimum<1.18` + `transformers<4.49`).

## Dashboard — Model manager

**Infrastructure → 6 · AirLLM → Model manager**

Same controls as Ollama: **Popular ▾**, **Install**, **Load**, **Unload**, **Remove**, **Show CLI**.

Use HuggingFace ids: `Qwen/Qwen2.5-7B-Instruct` (not Ollama `qwen2.5:7b` tags).

Curated list: `ecosystem-stack/config/popular-airllm-models.json`  
Pinned file: `ecosystem-stack/config/airllm-pinned-models.txt`

**Gated models** (Llama 3.1 70B, etc.) need `HF_TOKEN` passed when starting AirLLM.

## CLI

```bash
./leco-cli.sh airllm popular
./leco-cli.sh airllm install Qwen/Qwen2.5-0.5B-Instruct
./leco-cli.sh airllm load Qwen/Qwen2.5-0.5B-Instruct
./leco-cli.sh airllm unload Qwen/Qwen2.5-0.5B-Instruct
./leco-cli.sh airllm remove-model Qwen/Qwen2.5-0.5B-Instruct
./leco-cli.sh airllm logs          # watch long downloads
./leco-cli.sh airllm show-cmd Qwen/Qwen2.5-7B-Instruct
```

`install` dispatches the pull and returns quickly; download continues in the container.

## Storage

Docker volumes:

| Volume | Holds |
|--------|--------|
| `airllm_hf_cache` | HuggingFace weights |
| `airllm_layer_shards` | AirLLM per-layer shards |

Reset (delete all model data):

```bash
./leco-cli.sh airllm reset   # confirms first
```

## Traefik 404 on `airllm.lh`

Heal Traefik so `01-stack-core.yml` includes `airllm-http` routes:

```bash
./ecosystem-stack/ecosystem-stack.sh heal traefik
```

See also: [Troubleshooting](help:ts-common)
