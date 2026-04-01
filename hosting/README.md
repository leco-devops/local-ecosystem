# Hosting layout (`hosting/`)

Writable area under the **local-ecosystem** repo (mounted `/project` in the dashboard container). Sibling app repos under `workspace-parent` are often **read-only**; manifests and generated files must not be written there.

## Directories

| Path | Purpose |
|------|---------|
| **`app-available/<slug>/`** | Real content: `leco.app.yaml`, `leco.yaml`, optional `source` symlink to the real app tree, optional **config symlinks** (same relative paths as `configRefs` / compose / env / wrangler when those files exist), or an unzipped app tree. |
| **`app-enabled/<slug>`** | Symlink → `../app-available/<slug>`. The registry may reference `hosting/app-enabled/<slug>/leco.app.yaml` so paths stay under this indirection. |

## Register from a read-only path (e.g. `wsp:…`)

The dashboard **Detect** wizard can scan `wsp:MyRepo/subfolder`. On **Generate YAML** / **Save YAML**, if that directory is not writable, files are written under **`app-available/<slug>/`**, a **`source`** symlink points at the resolved app tree (so `dockerCompose` / `cloudflare` paths keep working), the dashboard adds **symlinks** into that folder for detected or declared config paths (when the targets exist), and **`app-enabled/<slug>`** is refreshed.

If you register a path that ends in a **`source/`** subfolder but **`wrangler.toml`** or **`docker-compose.yml`** live in the parent repo root, **`source`** is pointed at that **parent** so paths stay `wrangler.toml` / `docker-compose.yml` without `..`. The bridge’s **`root: source`** names the symlink file under `app-available/<slug>/`, not a literal `source` segment on the read-only tree.

Manifest copies in `hosting/` are **snapshots**; changes in the sibling repo are not synced automatically — register again if needed.

## Zip upload

`POST /api/hosted/upload-zip` (control token) accepts a zip into `app-available/<slug>/`, extracts in place, then **deletes the zip**. See `docs/DEPLOYMENT.md`.

## Remove / reset from the dashboard

**Hosted apps → Remove** (or **Reset**) runs **`leco-app ecosystem-unregister`**, which runs **`docker compose down`** first ( **`-v`** on **Reset** ), then registry removal, Traefik key cleanup when configured, optional local CF teardown, and deletion of **`hosting/app-enabled/<slug>`** / **`hosting/app-available/<slug>`** when the manifest lived under hosting. Compose **`down`** is skipped when the compose file is missing (same as **`leco-app down`**). See **`docs/LECO_APP_BLUEPRINT.md`**.
