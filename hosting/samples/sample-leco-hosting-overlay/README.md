# Sample: compose overlay beside `leco.app.yaml` (hosting-only)

Use this pattern when the **application repository** should stay free of LEco-specific Docker edits (Traefik `lh-network`, `*.lh` URLs, CORS), while **local-ecosystem** still drives deploy from `hosting/app-available/<slug>/`.

## Mechanism

1. **`leco.app.yaml`** stays minimal (name, `root: source`, `localHostProfile`, `configRefs`).
2. **`leco.yaml`** → `infrastructure.dockerCompose` points at the **upstream** compose file (usually via `composeFile` relative to the resolved app root, e.g. `../docker-compose.yml` through `source`).
3. **`additionalComposeFilesFromManifest`** lists one or more YAML files **relative to the manifest directory** (same folder as `leco.app.yaml`). Docker Compose merges them **after** the primary and `additionalComposeFiles` entries.

`docker compose` is run with **cwd = resolved app root**; only the `-f` paths for manifest-relative overlays are resolved from `hosting/app-available/<slug>/`.

## Files in this sample

| File | Purpose |
|------|---------|
| `docker-compose.leco-hosting.example.yml` | Copy to your app’s hosting folder as `docker-compose.leco-hosting.yml` and adjust **service names** to match upstream compose. |

## Overrides

- Edit the overlay YAML `environment` values, **or**
- Export variables before deploy (e.g. `REACT_APP_BACKEND_URL`, `CORS_ORIGINS`) — Compose `${VAR:-default}` picks them up.

## See also

- **`hosting/samples/sample-hosting-compose-entry/`** — when you must **remove** upstream host **`ports`** (e.g. **:80** vs Traefik), use **`composeFileFromManifest`** + **`include`** + **`ports: !reset []`** instead of relying on this overlay alone.
- `hosting/app-available/cvision/docker-compose.leco-hosting.yml` — working overlay for CrawlerVision-style stacks.
- **[docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md](../../../docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md)** — symptoms and fixes (502, probes, same-origin API).
- `docs/LECO_APP_BLUEPRINT.md` — full bridge / profile rules.
- `docs/DEPLOY_CLI.md` — `dockerCompose` field reference.
