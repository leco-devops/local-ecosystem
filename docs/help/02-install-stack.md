# Install — Ecosystem stack

From the **local-ecosystem** repository root:

```bash
./ecosystem-stack/ecosystem-stack.sh start
```

Or use the menu:

```bash
./leco-cli.sh
# → Stack → Start all
```

## What starts (order)

Traefik → Postgres → Ollama → **AirLLM** → Open WebUI → n8n → **service-dashboard** → cloudflare-local → infra compose → **file-transfer** (when enabled in platform config or full profile).

AirLLM is **not** optional in the default order but **must build on first start** (~150 MB CPU torch + deps). First build can take several minutes.

```bash
./leco-cli.sh airllm build    # visible build progress
./leco-cli.sh airllm start
```

## Verify

```bash
docker ps --format "table {{.Names}}\t{{.Status}}" | head -20
curl -fsS -H "Host: localhost.lh" http://127.0.0.1/api/overview | head -c 200
```

Open in browser:

- `https://localhost.lh` — LEco DevOps dashboard
- `https://ollama.lh` — Ollama API
- `https://airllm.lh` — AirLLM shim
- `https://ai.lh` — Open WebUI

## Traefik hosting files

On each Traefik start, `traefik/dynamic.yml` is copied to `hosting/traefik/01-stack-core.yml`. If `airllm.lh` returns **404** after an upgrade, heal:

```bash
./ecosystem-stack/ecosystem-stack.sh heal traefik
```

## Dashboard container

The dashboard runs as **`service-dashboard`** with the repo mounted at `/project`. Restart after UI/code changes:

```bash
./ecosystem-stack/ecosystem-stack.sh restart dashboard
```

Next: [LEco CLI install](help:install-cli) · [DNS](help:install-dns)
