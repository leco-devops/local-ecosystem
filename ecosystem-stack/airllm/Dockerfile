# AirLLM Ollama-compatible shim server.
#
# Runs as a Linux container (no MLX — Apple Silicon GPU is not reachable from
# Docker Desktop on macOS, and CUDA is not assumed). AirLLM uses PyTorch CPU
# inference here; large models work via layer-by-layer streaming, but
# throughput is bound by host RAM/CPU. For GPU acceleration, run this image on
# a Linux host with --gpus=all (CUDA wheels of torch will then activate).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=10 \
    HF_HUB_DOWNLOAD_TIMEOUT=600

# System deps: curl is needed for the HEALTHCHECK; build-essential is required
# for any wheels that have to be compiled from source on first install.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        git \
        build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the dep layer can be cached separately from the
# (frequently-changing) server source.
COPY requirements.txt /app/requirements.txt
# Install torch FIRST from the PyTorch CPU index so the rest of the resolve
# (airllm → torch) is already satisfied with the slim CPU wheel.
# Without this, `pip install torch` on aarch64/x86_64 pulls ~2GB of nvidia_*
# CUDA libraries (cudnn, nccl, cusparselt) that are useless in Docker Desktop
# on macOS (no GPU passthrough). For real CUDA boxes, override AIRLLM_TORCH_INDEX
# at build time, e.g. `--build-arg AIRLLM_TORCH_INDEX=https://download.pytorch.org/whl/cu121`.
ARG AIRLLM_TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN pip install --upgrade pip \
 && pip install --index-url "${AIRLLM_TORCH_INDEX}" "torch>=2.5.0" \
 && pip install -r /app/requirements.txt

# Shim source (single FastAPI app).
COPY server.py /app/server.py

# HF cache + AirLLM layer shards. The matching service script binds host
# volumes onto these paths so models survive container removal.
RUN mkdir -p /data/hf-cache /data/shards
ENV AIRLLM_HF_HOME=/data/hf-cache \
    AIRLLM_LAYER_SHARDS_DIR=/data/shards \
    AIRLLM_PORT=11435

EXPOSE 11435

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:11435/api/tags >/dev/null || exit 1

CMD ["python", "-u", "server.py"]
