# LEco DevOps manifest samples

Reference **`leco.app.yaml`** + **`leco.yaml`** pairs and optional compose overlays. These live under **`hosting/samples/`** (sibling of **`hosting/app-available/`**) so the Hosted apps dashboard does not treat them as staging apps.

Copy a folder into **`hosting/app-available/<your-slug>/`** when you want a writable materialization, or use the paths only as documentation. The CLI **`scaffold`** command automates this with placeholder replacement:

```bash
leco-devops scaffold myapp -E /path/to/local-ecosystem --template sample-node-varnish-multiprocess --source-path /abs/path/to/source
```

| Folder | Purpose |
|--------|---------|
| **`sample-cloudflare-application/`** | Cloudflare Worker: `wrangler.toml` + KV / R2 / D1 preview and local CF settings; dedicated CF compose example. |
| **`sample-wordpress-application/`** | WordPress: Docker Compose, Traefik host, frontend/admin URLs. |
| **`sample-nodejs-data-stack/`** | Node API + Redis, MySQL, MongoDB, NGINX, Varnish (see `notes`; compose defines services). Split Traefik route: NGINX UI + `/api` → Node. |
| **`sample-compose-only/`** | Compose-only baseline (no Wrangler). |
| **`sample-wrangler-local-cf/`** | Full `wrangler.toml` + matching `wranglerBindingPreview` (3× KV, R2, D1). |
| **`sample-leco-hosting-overlay/`** | Example **`docker-compose.leco-hosting.example.yml`** pattern for **`additionalComposeFilesFromManifest`** (no full app manifest): `lh-network`, same-origin env, and `ports: !reset []` host-port stripping. |
| **`sample-hosting-compose-entry/`** | Hosting-only **primary** compose via **`composeFileFromManifest`**: **`include`** upstream **`source/docker-compose.yml`**, **`ports: !reset []`**, **`lh-network`** — no edits in the upstream repo (e.g. Headwind **:80** vs Traefik). |
| **`sample-node-varnish-multiprocess/`** | Multi-process Node.js + Varnish HTTP cache + MongoDB + Redis. Full 7-file template: runtime config preloader (`leco-docker-preload.js`), `conf/varnish/` custom service config, hosting overlay with apt-get/npm commands, shared node_modules volume. |
| **`sample-cf-worker-runtime/`** | Local edge runtime: declares an **`infrastructure.runtimes[]`** entry (`type: cloudflare-workers`) and **`routing.entries[].upstream[]`** so `<slug>.lh/api/*` hits a locally-run Cloudflare Worker instead of a classic backend — production-faithful 404s, no upstream changes. |

See also **`../app-available/README.md`** for the materialization layout, **`../app-available/cvision/`** for a working overlay example, and **[docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md](../../docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md)** for Traefik troubleshooting.
