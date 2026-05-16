# Cloudflare Pages runtime (`leco/runtime-cloudflare-pages`)

Serves a built static directory via `wrangler pages dev` for monorepos that keep
`wrangler.pages.toml` under `infra/` (Raven-style).

Environment (set by `dashboard/leco_runtimes/cloudflare_pages.py`):

| Variable | Purpose |
|----------|---------|
| `LECO_WRANGLER_CONFIG` | Path to `wrangler.pages.toml` inside the container |
| `LECO_PAGES_ASSET_DIR` | Optional absolute path to `dist/` (else parsed from TOML) |
| `LECO_PORT` | Listen port (default `8791`) |

If `pages_build_output_dir` is empty, the entrypoint runs `pnpm`/`npm` build from `/app` when possible.
