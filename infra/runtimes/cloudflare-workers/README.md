# LEco DevOps — Cloudflare Workers local runtime

Generic Wrangler/Miniflare runtime that runs **any** Cloudflare Worker locally
behind Traefik. Image name (consumed by
[`dashboard/leco_runtimes/cloudflare_workers.py`](../../../dashboard/leco_runtimes/cloudflare_workers.py)):

```
leco/runtime-cloudflare-workers:latest
```

The dashboard builds it on demand from this directory the first time a hosted
app declares an `infrastructure.runtimes[]` entry with `type: cloudflare-workers`
— operators rarely need to touch it.

## Why a separate image

LEco's mission is **don't change the upstream app**. The image:

- Ships `wrangler@^3` globally.
- Bind-mounts the upstream worker source at `/app` (read-only in spirit).
- Masks `/app/node_modules` and `/app/.wrangler` with LEco-owned named volumes
  so `npm ci` and Miniflare state never write into the upstream repo.
- Runs `wrangler dev --local --persist-to .wrangler/state` so KV/R2/D1 are
  file-backed and survive container restarts.

## Container contract

Environment variables (set by the adapter):

| Var | Default | Purpose |
|-----|---------|---------|
| `LECO_APP_SLUG` | — | App slug (logs only) |
| `LECO_RUNTIME_ID` | — | Runtime id from manifest (logs only) |
| `LECO_PORT` | `8787` | Bind port (`0.0.0.0`) — Traefik forwards here |
| `LECO_WRANGLER_CONFIG` | `/app/wrangler.toml` | Path to `wrangler.toml` inside the container |

Volume contract:

| Mount | Source | Purpose |
|-------|--------|---------|
| `/app` | bind: `<manifest_root>/<sourceDir>` | Upstream worker source |
| `/app/node_modules` | named volume `leco-rt-<slug>-<runtime>-node-modules` | npm install target |
| `/app/.wrangler` | named volume `leco-rt-<slug>-<runtime>-wrangler-state` | Miniflare KV/R2/D1 state |
| `/app/.dev.vars` (optional) | bind: `hosting/app-available/<slug>/.dev.vars` (read-only) | Local-only secrets |

## Build

The dashboard's `docker compose up` builds the image on first use thanks to the
`build:` block emitted by the adapter (`infra/runtimes/cloudflare-workers/`).
You can pre-build:

```
docker build -t leco/runtime-cloudflare-workers:latest \
    infra/runtimes/cloudflare-workers
```

## Logs / debugging

```
docker logs -f leco-rt-<slug>-<runtime>
```

The entrypoint emits one `[leco-runtime cf-workers] …` line per phase so
"missing wrangler.toml", "installing node_modules", and "wrangler dev" are
easy to spot in CI logs.
