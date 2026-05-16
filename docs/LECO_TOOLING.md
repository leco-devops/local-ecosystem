# LEco tooling reference

This document explains the LEco toolchain in one place: CLI, manifests, registry, and dashboard integration.

## 1) Tooling components

- CLI entrypoint: `leco-devops`.
- Package location: `tools/deploy-cli/`.
- Dashboard bridge: `dashboard/leco_subprocess.py` invokes CLI commands from LEco DevOps.

## 2) Core artifacts

| Artifact | Purpose |
| ----- | ----- |
| `leco.app.yaml` | App bridge manifest (identity + profile link + root) |
| `leco.yaml` | Profile/infrastructure details (routing, compose, cloudflare, urls) |
| `config/leco-registry.yaml` | Registered app inventory consumed by LEco DevOps |
| `traefik/dynamic.yml` | Canonical stack routes in git (copied to **`hosting/traefik/01-stack-core.yml`** on Traefik start) |
| `hosting/traefik/dynamic.yml` | Writable merge file for **`leco-devops`** / LEco DevOps Routes (empty document **`{}`**, not **`http: {}`**) |
| `hosting/app-available/<slug>/docker-compose.leco-hosting.yml` (optional) | Compose overlay via **`additionalComposeFilesFromManifest`** — LEco-only network/env without editing the upstream app repo |
| `hosting/app-available/<slug>/` | Materialized writable hosted app layout |

## 3) Common lifecycle commands

```bash
# from app repo
leco-devops detect
leco-devops init
leco-devops deploy
leco-devops ecosystem-register --ecosystem-root /path/to/local-ecosystem
leco-devops ecosystem-unregister <slug> --ecosystem-root /path/to/local-ecosystem
```

## 4) Dashboard <-> CLI relationship

- Hosted registration in LEco DevOps calls LEco APIs, which call `leco-devops`.
- Hosted controls (deploy/stop/down/remove/reset) eventually route into CLI and/or compose execution.
- Docs tab and routes tab expose manifest/fragment operations that map to CLI behavior.

## 5) Recommended operator flow

1. Detect app root and generate manifest/profile.
2. Validate and save YAML.
3. Register app to ecosystem registry.
4. Deploy and verify health/metrics/logs from Hosted apps.
5. Remove/reset via Hosted apps or `ecosystem-unregister` when offboarding.

## 6) Where to go deeper

- Technical command/YAML reference: [`DEPLOY_CLI.md`](DEPLOY_CLI.md)
- User workflow guide: [`LECO_USER_MANUAL.md`](LECO_USER_MANUAL.md)
- Architecture context: [`ARCHITECTURE.md`](ARCHITECTURE.md), [`HLD.md`](HLD.md), [`LLD.md`](LLD.md)
