# Install — DNS (`*.lh`) and HTTPS

Local hostnames use the **`.lh`** TLD (e.g. `localhost.lh`, `ollama.lh`, `airllm.lh`).

## macOS / Linux — `/etc/hosts`

Add lines (or use your project's install script if provided):

```text
127.0.0.1 localhost.lh dashboard.lh traefik.lh ollama.lh airllm.lh ai.lh n8n.lh paperclip.lh
```

Traefik terminates TLS with the certificate pair in the repo's `certs/` directory. Generate it with:

```bash
./certs/generate-certs.sh
./ecosystem-stack/ecosystem-stack.sh restart traefik
```

The script discovers every `*.lh` hostname the stack serves — Traefik routers, the app registry, materialized apps — and issues **one certificate with an explicit SAN per hostname**, verifying coverage before it finishes. With the mkcert CA installed (`mkcert -install`), browsers show a normal padlock; **you should not have to click through a warning.**

> **Do not run `mkcert "*.lh"`.** A wildcard directly below a top-level domain is rejected by every TLS client, so such a certificate matches **nothing** — not even `dashboard.lh`. The chain verifies while the hostname check fails, which is exactly the "still not secure" symptom people spend hours on. Earlier versions of this guide recommended it.

**After onboarding an app with a new hostname**, re-run the script and restart Traefik — a hostname missing from the certificate will fail validation.

Check it properly, without `-k` (which hides the failure):

```bash
curl -sS -o /dev/null -w '%{http_code} verify=%{ssl_verify_result}\n' https://dashboard.lh
# 200 verify=0   ← trusted chain AND matching hostname
```

On a **real domain** none of this applies: mkcert is local-only and certificates come from ACME, Cloudflare, or your own CA. See [Platform tab](help:dash-platform) and [Production hardening](/?tab=docsTab&doc=production-hardening).

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
