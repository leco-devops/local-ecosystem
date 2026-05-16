# Hosting layout (`hosting/`)

Writable area under the **local-ecosystem** repo (mounted `/project` in the dashboard container). Sibling app repos under `workspace-parent` are often **read-only**; manifests and generated files must not be written there.

## Directories

| Path | Purpose |
|------|---------|
| **`samples/`** | Reference **`leco.app.yaml`** / **`leco.yaml`** packs (not under **`app-available/`**); the dashboard does not list them as staging apps. See **`samples/README.md`**. |
| **`app-available/<slug>/`** | Real content: `leco.app.yaml`, `leco.yaml`, optional `source` symlink to the real app tree, optional **config symlinks** (paths from `configRefs`, every `infrastructure.runtimes[].config`, and any `wrangler.*.toml` discovered under the resolved app root — refreshed on **Generate YAML** / **Save YAML**), or an unzipped app tree. |
| **`app-available/<slug>/docker-compose.leco-hosting.yml`** (optional) | Compose **merge** file loaded via **`leco.yaml` → `infrastructure.dockerCompose.additionalComposeFilesFromManifest`**: attach **`lh-network`**, strip upstream host `ports` with **`!reset`**, and set `*.lh` / Traefik-facing env defaults, without changing the upstream app repo. See **`hosting/samples/sample-leco-hosting-overlay/`**, **`app-available/cvision/docker-compose.leco-hosting.yml`**, and **[docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md](../docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md)**. |

## Register from a read-only path (e.g. `wsp:…`)

The dashboard **Detect** wizard can scan `wsp:MyRepo/subfolder`. On **Generate YAML** / **Save YAML**, if that directory is not writable, files are written under **`app-available/<slug>/`**, a **`source`** symlink points at the resolved app tree (so `dockerCompose` / `cloudflare` paths keep working), and the dashboard adds **symlinks** into that folder for detected or declared config paths (when the targets exist). Multi-Wrangler monorepos (`infra/wrangler.api.toml`, `wrangler.onboarding.toml`, …) get one symlink per file automatically — see **`docs/help/12-multi-wrangler-monorepo.md`**. The registry points directly at `hosting/app-available/<slug>/leco.app.yaml`.

If you register a path that ends in a **`source/`** subfolder but **`wrangler.toml`** or **`docker-compose.yml`** live in the parent repo root, **`source`** is pointed at that **parent** so paths stay `wrangler.toml` / `docker-compose.yml` without `..`. The bridge’s **`root: source`** names the symlink file under `app-available/<slug>/`, not a literal `source` segment on the read-only tree.

Manifest copies in `hosting/` are **snapshots**; changes in the sibling repo are not synced automatically — register again if needed.

## Zip upload

`POST /api/hosted/upload-zip` (control token) accepts a zip into `app-available/<slug>/`, extracts in place, then **deletes the zip**. See `docs/DEPLOYMENT.md`.

## Remove / reset from the dashboard

**Hosted apps → Remove** (or **Reset**) runs **`leco-devops ecosystem-unregister`**, which runs **local CF teardown** first when enabled, then **`docker compose down`** ( **`-v`** on **Reset** ), then Traefik key cleanup when configured, registry removal, and deletion of **`hosting/app-available/<slug>`** when the manifest lived under hosting. Compose **`down`** is skipped when the compose file is missing (same as **`leco-devops down`**). See **`docs/LECO_APP_BLUEPRINT.md`**.
