# Platform layout (`platform/`)

Writable area for **cloud/local platform** settings and **isolated dev stacks** (separate from **hosted apps** under `hosting/app-available/`).

**Official repository:** [https://github.com/leco-devops/local-ecosystem](https://github.com/leco-devops/local-ecosystem)

Dev stacks are full Docker Compose projects on their own networks — useful when teams need different databases or runtimes on one machine without sharing a single global Postgres or MySQL container.

## Directories

| Path | Purpose |
|------|---------|
| **`dev-stacks/`** | One folder per stack: generated `docker-compose.yml`, `stack.yaml`, and optional edge configs (`nginx/`, `varnish/`). See **`dev-stacks/README.md`**. |
| **`dev-stacks/<id>/`** | Runtime files for stack `<id>` (compose project `leco-devstack-<id>`). Created by the dashboard **Platform** tab or `leco-devops dev-stack create`. |

## Related configuration (repo root)

| Path | Purpose |
|------|---------|
| **`config/leco-platform.yaml`** | Deployment mode, `base_domain`, TLS, enabled ecosystem services, `dev_stacks[]` registry (gitignored per machine; copy from **`config/leco-platform.yaml.example`**). |
| **`hosting/traefik/20-dev-stacks.yml`** | Generated HTTP routes for all stacks (regenerated on create/destroy/repair). |
| **`ecosystem-stack/config/component-catalog.yaml`** | Component versions for custom stacks. |
| **`ecosystem-stack/config/dev-stack-presets.yaml`** | Quick presets (WordPress, Magento, Laravel, infrastructure levels, …). |

## Hosted apps vs dev stacks

| Model | Path | Registry |
|-------|------|----------|
| **Hosted app** | `hosting/app-available/<slug>/` | `config/leco-registry.yaml` |
| **Dev stack** | `platform/dev-stacks/<id>/` | `config/leco-platform.yaml` → `dev_stacks[]` |

Optional binding: set **`platform.devStackId`** in a hosted app’s **`leco.yaml`** to attach deploy to a stack’s network and connection env.

## Create and operate stacks

- **Dashboard:** **Platform** tab → **Dev stack builder** → **Your dev stacks** (Start, Stop, Repair, Reinstall, Destroy).
- **CLI:** `leco-devops dev-stack …` with `LECO_ECOSYSTEM_ROOT` set — see **[docs/DEPLOY_CLI.md](../docs/DEPLOY_CLI.md)**.

Guides: **[docs/help/03-platform-tab.md](../docs/help/03-platform-tab.md)** · **[docs/DEV_STACK_ISOLATION.md](../docs/DEV_STACK_ISOLATION.md)**.

## Git and backups

Per-machine stack trees under **`platform/dev-stacks/*`** are **gitignored** (see root **`.gitignore`**). Only **`platform/README.md`**, **`dev-stacks/README.md`**, and **`.gitkeep`** placeholders are tracked. Back up important stacks locally or export compose before **Destroy**.
