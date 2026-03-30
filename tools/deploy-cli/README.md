# leco-app — multi-app deploy CLI (Wrangler-style, plug-and-play)

`leco-app` inspects an application repository, writes a small manifest (`leco.app.yaml`), and runs **Docker Compose** and optionally **Wrangler** lifecycle commands. It is **orthogonal** to the local-ecosystem core: third-party apps stay in their own repos and compose files.

## Resource model (one package per app)

- **One manifest per application** lists what that app uses: compose file, optional Wrangler config, optional Traefik routing hints, health URLs.
- The CLI **does not** provision a duplicate MinIO/Valkey/MySQL stack per app by default. Your app’s **own** `docker-compose.yml` defines its containers (Mongo, NGINX, API, …), or the manifest only **references** shared services you run separately (e.g. infra MySQL, `kv.lh`).
- **Multiple Workers** locally = multiple **projects** (separate manifests / compose files), not one command spawning many Workers unless your compose defines that.

## Install

From the local-ecosystem repo:

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

leco-app deploy            # docker compose up -d --build
leco-app status
leco-app logs -f

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
| `routing.entries` | Hostname + backend Docker DNS + port for Traefik fragment |
| `healthcheckUrls` | URLs probed by `leco-app status` |

## Commands

| Command | Description |
|---------|-------------|
| `init` | Detect compose + wrangler; prompts; write manifest |
| `deploy` | `docker compose up -d --build` |
| `stop` | `docker compose stop` |
| `down` | `docker compose down` (`-v` optional) |
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
