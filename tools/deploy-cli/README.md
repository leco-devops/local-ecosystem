# leco-app — multi-app deploy CLI (Wrangler-style, plug-and-play)

`leco-app` inspects an application repository, writes a small manifest (`leco.app.yaml`), and runs **Docker Compose** and optionally **Wrangler** lifecycle commands. It is **orthogonal** to the local-ecosystem core: third-party apps stay in their own repos and compose files.

## Resource model (one package per app)

- **One manifest per application** lists what that app uses: compose file, optional Wrangler config, optional Traefik routing hints, health URLs.
- The CLI **does not** start MinIO/Valkey for you — that is **local-ecosystem’s** `cloudflare-local` stack. On **`leco-app init`** (prompt or `--provision-local-cf`) and **`leco-app ecosystem-register`** (default on), it **creates dedicated resources** on those adapters from your **wrangler.toml** bindings: **KV namespaces** (per app + binding + id fragment), **R2 buckets**, and **D1 SQLite DBs** using the same **`bucket_name` / `database_name`** as in Wrangler. Your **`wrangler.toml` is never modified.** Output: **`leco.local-cf.yaml`** next to the manifest (URLs + local names). Override bases with **`LECO_LOCAL_KV_URL`**, **`LECO_LOCAL_R2_URL`**, **`LECO_LOCAL_D1_URL`**; use **`LECO_LOCAL_CF_INSECURE_SSL=1`** only if you must skip TLS verify.
- **Multiple Workers** locally = multiple **projects** (separate manifests / compose files), not one command spawning many Workers unless your compose defines that.

## Install

Install from **this directory** (`deploy-cli/`), not from the parent `tools/` folder — only here is `pyproject.toml` present.

From the **local-ecosystem repository root**:

```bash
cd tools/deploy-cli
pip install -e .
leco-app --help
```

Python **3.11+** required.

## Quick start

```bash
cd /path/to/your/app
leco-app init              # interactive wizard
# or
leco-app init -y           # defaults only (compose + wrangler detection)

# During init, if you add Traefik routes: press Enter on an empty hostname to stop adding routes.
# Choose "split route" for React + API: generates frontend + apiBackend in leco.app.yaml and
# traefik-fragment output with Host+PathPrefix(/api) → backend, Host → UI. Put those containers on lh-network.

leco-app deploy            # docker compose up -d --build
leco-app status
leco-app logs -f
leco-app down              # compose down only

# Remove app + Traefik routes (writes dynamic.yml.bak, then docker compose down)
leco-app offload --traefik-dynamic /path/to/local-ecosystem/traefik/dynamic.yml

leco-app traefik-fragment  # print YAML to paste into traefik/dynamic.yml
leco-app traefik-fragment -o /tmp/traefik-snippet.yml

leco-app cf-secrets-checklist --env staging
leco-app cf-deploy --env staging
leco-app cf-deploy --env production --confirm-production
```

State/metadata: `~/.local/share/leco/apps/<slug>/` (override with `XDG_DATA_HOME`).

## Manifest (`leco.app.yaml`)

Written next to the app root (or path given to `--out`). Uses **camelCase** keys for compatibility with common tooling conventions:

| Field | Purpose |
|-------|---------|
| `name` | Slug; used for state directory |
| `root` | `.` = manifest directory |
| `dockerCompose.composeFile` | Path to compose file relative to `root` |
| `dockerCompose.envFile` | Optional `--env-file` |
| `dockerCompose.projectName` | Optional `docker compose -p` |
| `dockerCompose.profiles` | Compose profiles |
| `cloudflare.wranglerConfig` | Path to `wrangler.toml` |
| `cloudflare.wranglerEnv` | Default `--env` for Wrangler |
| `routing.entries` | Traefik fragment: legacy backend **or** split `frontend` + `apiBackend` |
| `traefikCleanup` | Optional explicit router/service keys for `leco-app offload` when names differ from fragment defaults |
| `healthcheckUrls` | URLs probed by `leco-app status` |

## Commands

| Command | Description |
|---------|-------------|
| `init` | Detect compose + wrangler; prompts; write manifest |
| `deploy` | `docker compose up -d --build` |
| `stop` | `docker compose stop` |
| `down` | `docker compose down` (`-v` optional) |
| `offload` | `compose down` + optional `--traefik-dynamic` to strip routes (see DEPLOY_CLI.md) |
| `ecosystem-register` | Add app to `local-ecosystem/config/leco-registry.yaml`; also provisions local KV/R2/D1 from Wrangler unless `--no-provision-local-cf` |
| `provision-local-cf` | Re-run KV/R2/D1 creation from manifest’s Wrangler config |
| `ecosystem-unregister` | Remove registry id; by default strips Traefik keys from `traefik/dynamic.yml` and deletes `leco.local-cf.yaml` resources (`--no-strip-traefik` / `--no-clean-local-cf` to skip) |
| `logs` | `docker compose logs` (`-f`, `--tail`, `--service`) |
| `status` | `docker compose ps` + optional HTTP checks |
| `traefik-fragment` | Emit YAML for manual merge into `traefik/dynamic.yml` |
| `cf-deploy` | `wrangler deploy -c …` (requires `wrangler login`) |
| `cf-secrets-checklist` | Heuristic list of `[vars]` keys that may need `wrangler secret put` |

## Traefik

The CLI **does not auto-edit** `traefik/dynamic.yml` (merge conflicts). Use `traefik-fragment` and paste after backing up the file.

## See also

- [docs/DEPLOY_CLI.md](../../docs/DEPLOY_CLI.md) in the repo root
- [docs/DEPLOY_CUSTOM_APPS.md](../../docs/DEPLOY_CUSTOM_APPS.md)
