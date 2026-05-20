# Install — DNS (`*.lh`) and HTTPS

Local hostnames use the **`.lh`** TLD (e.g. `localhost.lh`, `ollama.lh`, `airllm.lh`).

## macOS / Linux — `/etc/hosts`

Add lines (or use your project's install script if provided):

```text
127.0.0.1 localhost.lh dashboard.lh traefik.lh ollama.lh airllm.lh ai.lh n8n.lh
```

Traefik terminates TLS with certs from the repo `certs/` directory (self-signed). Browsers will warn once — proceed for local dev.

## Test routing

```bash
curl -kfsS https://airllm.lh/health
curl -fsS -H "Host: ollama.lh" http://127.0.0.1/api/tags
```

## 404 on a `*.lh` URL

1. Container running? `docker ps --filter name=airllm`
2. On `lh-network`? `docker network inspect lh-network`
3. Traefik route present? `./ecosystem-stack/ecosystem-stack.sh heal traefik`
4. See [502 / routing](help:ts-502)

Next: [Dashboard tour](help:dash-overview)
