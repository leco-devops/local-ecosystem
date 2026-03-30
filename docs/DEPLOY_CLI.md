# leco-app — deploy CLI

**leco-app** is a small command-line tool (under `tools/deploy-cli/`) that helps you deploy **third-party** applications in a **plug-and-play** way: one manifest per app, Docker Compose lifecycle, optional Cloudflare Wrangler deploy, and optional Traefik YAML fragments — without folding each app into the local-ecosystem `ai-stack` or `core.sh`.

## Install

```bash
cd tools/deploy-cli
pip install -e .
```

Requires **Python 3.11+** and **Docker** with the Compose v2 plugin (`docker compose`).

## Usage summary

```bash
cd /path/to/application
leco-app init          # wizard: detects docker-compose + wrangler.toml
leco-app deploy
leco-app status
leco-app logs -f
leco-app down
```

From another directory, pass the manifest:

```bash
leco-app deploy --manifest /path/to/app/leco.app.yaml
```

Or search upward from `--cwd` for `leco.app.yaml`:

```bash
leco-app deploy --cwd /path/to/app
```

## What gets created

| Artifact | Location |
|----------|----------|
| Manifest | `leco.app.yaml` (default: application root) |
| Tool state | `~/.local/share/leco/apps/<name>/` |

## Cloudflare

If `wrangler.toml` or `cloudflare/wrangler.toml` is detected during `init`, the manifest can reference it.

```bash
leco-app cf-secrets-checklist --env staging
leco-app cf-deploy --env staging
leco-app cf-deploy --env production --confirm-production
```

Production deploys require **`--confirm-production`** to reduce accidents.

## Traefik (`*.lh`)

```bash
leco-app traefik-fragment -o /tmp/myapp-traefik.yml
```

Merge the output into `traefik/dynamic.yml` manually (backup first). Traefik watches the file; see [DEPLOY_CUSTOM_APPS.md](DEPLOY_CUSTOM_APPS.md).

## Relationship to local-ecosystem

| Component | Role |
|---------|------|
| `ai-stack`, Dashboard Control | **First-party** stacks only |
| **leco-app** | **External** repos: compose + wrangler + optional routing hints |

Full design notes and the **per-app vs shared platform** model: [tools/deploy-cli/README.md](../tools/deploy-cli/README.md).
