# Cloudflare local

Optional **KV, R2, D1**, Workers demo, browser rendering, and autoscaler on `*.lh` for Workers development.

## Start everything (recommended)

From the repo root, start the **full platform** (Traefik, Postgres, Ollama, WebUI, n8n, LEco DevOps, Cloudflare-local, infra add-ons):

```bash
./ecosystem-stack/ecosystem-stack.sh start
```

Or use **Infrastructure → Start stacks** in LEco DevOps (Start ecosystem / Start Cloudflare local / Start infra add-ons).

Cloudflare-local only:

```bash
./ecosystem-stack/ecosystem-stack.sh start cloudflare-local
```

**Infrastructure → 4 · Cloudflare local** shows reachability, per-container Docker status, and quick links.

`leco-devops provision-local-cf` creates namespaces/buckets from `wrangler.toml` bindings.

## Cloudflare-local compose stack (9 containers)

| Container | Role | URL / access |
|-----------|------|----------------|
| `minio` | R2 backend (S3) | **Web UI:** http://minio-console.lh · **S3 API:** http://s3.lh (SDK/CLI; browsers redirect to minio-console.lh) |
| `valkey` | KV backend | TCP `valkey.lh:6380` |
| `r2-adapter` | R2-style API | http://r2.lh |
| `kv-adapter` | KV-style API | http://kv.lh |
| `d1-adapter` | D1-style API | http://d1.lh |
| `workers-runtime` | Miniflare demo | http://workers.lh |
| `browser-rendering-local` | Headless browser | http://browser.lh |
| `autoscaler` | Replica scaler demo | http://autoscale.lh |
| `autoscale-demo` | Scaler target (internal) | — |

## Wrangler bindings (provision / runtime)

| Wrangler binding | Local (LEco) | Status |
|------------------|--------------|--------|
| `[[kv_namespaces]]` | kv.lh | implemented |
| `[[r2_buckets]]` | r2.lh | implemented |
| `[[d1_databases]]` | d1.lh | implemented |
| `[browser]` | browser.lh (Wrangler bridge when `LECO_LOCAL_BROWSER_URL` set) | partial |
| `[[queues]]` | planned (infra Redis backend) | planned |
| `[[hyperdrive]]` | postgres.lh / infra mysql (`.dev.vars` DSN) | partial |
| `send_email` | Mailpit SMTP (mailpit:1025) | partial |

## Related ecosystem services (separate compose)

Started with `./ecosystem-stack/ecosystem-stack.sh start infra`:

| Service | Container | URL |
|---------|-----------|-----|
| MySQL | mysql | mysql.lh:3306 |
| Redis | redis | redis.lh:6379 |
| Mailpit | mailpit | http://mail.lh |
| Adminer | adminer | http://adminer.lh |
| Redis Commander | redis-commander | http://redis-ui.lh |
| Cache lab | cache-varnish / cache-nginx | http://cache.lh |

Full mapping: **Docs** tab → *CF ↔ LEco service map*, or `docs/CF_LECO_SERVICE_MAP.md`.

Full docs: **Docs** tab → *Cloudflare Local* section.
