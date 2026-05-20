# Dev stacks (`platform/dev-stacks/`)

Each subdirectory is one **isolated dev stack**: its own Compose project, internal Docker network, and optional Traefik route on `lh-network`.

## Per-stack layout

| File / folder | Purpose |
|---------------|---------|
| **`docker-compose.yml`** | Services for this stack (databases, app, edge). Compose project name: `leco-devstack-<id>`. |
| **`stack.yaml`** | Metadata: `id`, `name`, `template` or `components[]`, `project`, `internal_network`, `sample_data`, etc. |
| **`nginx/`**, **`varnish/`** | Optional edge configs (e.g. Magento full stack). |
| **Other paths** | Template-specific files; edit via dashboard **Advanced — configuration & files** or CLI. |

Stacks are **generated** from presets (`ecosystem-stack/config/dev-stack-presets.yaml`), ready templates (WordPress, Magento, …), or custom component picks (`component-catalog.yaml`). **Reinstall** regenerates these files from the template; **Repair** updates images and routing without wiping volumes.

## Networks

- **`leco-devstack-<id>-internal`** — databases and app containers (not published on host ports by default).
- **`lh-network`** — only services Traefik should reach (HTTP on `http://<stackId>.lh` locally).

## Registry and routing

| What | Where |
|------|--------|
| Stack list + state | `config/leco-platform.yaml` → `dev_stacks[]` |
| Public URL | `http://<id>.lh` (local) or `https://<id>.<base_domain>` (cloud) |
| Traefik routes | `hosting/traefik/20-dev-stacks.yml` (auto-generated) |

This is **not** the same as **`hosting/app-available/<slug>/`**, which holds **hosted app** manifests (`leco.app.yaml` + `leco.yaml`). See **`../README.md`** and **`hosting/app-available/README.md`**.

## Example folders

After you create stacks from the **Platform** tab or CLI, you will see directories such as:

| Folder | Typical preset |
|--------|----------------|
| **`wordpress/`** | WordPress (+ optional sample install) |
| **`magento-full/`** | Magento with Varnish + Nginx |
| **`billing/`** | Custom component bundle (e.g. Postgres + Redis + Node) |

Folder names match the **stack id** you chose at create time (lowercase slug).

## Lifecycle (dashboard or CLI)

| Action | Effect |
|--------|--------|
| **Start** | `compose up -d`; URL repair when CMS is ready |
| **Stop** | Stop containers; **keeps volumes** |
| **Repair** | Fix images, Traefik, `lh-network`; **keeps data** and manual edits |
| **Reinstall** | Regenerate from template + `down -v` — **wipes data** |
| **Destroy** | Remove this directory, volumes, registry row, Traefik routes |

## Bind a hosted app

In the app profile:

```yaml
platform:
  devStackId: wordpress
```

Or: `leco-devops platform bind wordpress -f hosting/app-available/myapp/leco.app.yaml`

See **[docs/DEV_STACK_ISOLATION.md](../../docs/DEV_STACK_ISOLATION.md)** and **[docs/help/03-platform-tab.md](../../docs/help/03-platform-tab.md)**.
