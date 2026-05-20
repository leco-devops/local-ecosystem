# Dev stack isolation

A **dev stack** is an isolated Docker Compose project (`leco-devstack-<id>`) with its own internal network. Use it when multiple apps on one VM need different **Node**, **Python**, or database versions without sharing one global MySQL/Node container.

## Architecture

- **Internal network:** `leco-devstack-<id>-internal` — databases and toolchains talk here.
- **Edge network:** `lh-network` — only HTTP services that Traefik should reach join this network.
- **No host ports** on databases by default — avoids port collisions on the VM.

## Create a stack

**Dashboard:** **Platform** tab → **Dev stack builder** (collapsible panel).

- **Quick preset** — infrastructure levels, LAMP/MEAN, ready CMS (WordPress, Magento, …), or **application frameworks** (Laravel, Django, NestJS, …).
- **Custom components** — pick from `component-catalog.yaml` (e.g. `postgres:16`, `redis:7`, `node:20`).
- Optional **sample data** for supported CMS presets.

User guide: [help/03-platform-tab.md](help/03-platform-tab.md).

**API:**

```json
POST /api/dev-stacks
{
  "id": "billing",
  "name": "Billing team",
  "components": [
    {"id": "postgres", "version": "16"},
    {"id": "redis", "version": "7"},
    {"id": "node", "version": "20"}
  ]
}
```

Files land in `platform/dev-stacks/billing/` (`docker-compose.yml`, `stack.yaml`).

Layout guide: [platform/README.md](../platform/README.md) · [platform/dev-stacks/README.md](../platform/dev-stacks/README.md).

## Where configuration lives (not under `hosting/app-available/`)

| What | Path |
|------|------|
| Stack compose + metadata | `platform/dev-stacks/<id>/` (`docker-compose.yml`, `stack.yaml`, optional `varnish/`, `nginx/`) |
| Platform registry row | `config/leco-platform.yaml` → `dev_stacks[]` |
| Traefik HTTP routes | `hosting/traefik/20-dev-stacks.yml` (generated for all stacks) |

**Hosted apps** use `hosting/app-available/<slug>/` and `config/leco-registry.yaml`. Dev stacks are a separate model.

**Dashboard:** Platform → each stack card shows **Networking** (Traefik → app flow), **Admin & credentials** (open admin, copy magic link, reset), **Quick open** (storefront, Adminer, Redis Commander), and **Data stores** (connection strings). **Advanced — configuration & files** (view paths, edit stack files, view shared Traefik/platform files).

| Action | Effect |
|--------|--------|
| **Repair** | Apply LEco config fixes (images, edge configs), sync Traefik, reconnect `lh-network`, `compose up -d`, public URL repair — **keeps volumes** and **manual Advanced edits** |
| **Reinstall** | Regenerate files from template (reverts manual edits), `compose down -v`, fresh deploy and reconfigure — **wipes DB/app data** (use after wrong MariaDB version or broken install) |

**Framework stacks** (Platform → Stack preset → *Application frameworks*): Yii2, CakePHP, Symfony, Laravel, Django, Ruby on Rails, NestJS, FastAPI, Flask, Express. First **Start** installs dependencies inside the app container; use `docker compose logs -f app` to watch bootstrap.

## Lifecycle

| Action | Effect |
|--------|--------|
| `start` | `docker compose -p leco-devstack-billing up -d`; image preflight; CMS URL repair when install is ready |
| `stop` | Stops billing stack; other stacks keep running; **volumes retained** |
| `repair` | In-place fixes (images, edge configs), Traefik sync, `lh-network`, `compose up`, URL repair — **keeps volumes and manual file edits** |
| `reinstall` | Regenerate from template, `compose down -v`, full start — **wipes data** (`redeploy` API alias) |
| `destroy` | `compose down -v --remove-orphans`, prunes leftover project containers/volumes/networks, removes `platform/dev-stacks/<id>/`, drops the stack from `config/leco-platform.yaml`, and regenerates `hosting/traefik/20-dev-stacks.yml` |

The dashboard uses **`POST /api/dev-stacks/<id>/action/stream`** for live compose logs during these actions.

**Image preflight:** On **Start**, LEco rewrites deprecated image names in `docker-compose.yml` (e.g. `bitnami/magento` → `bitnamilegacy/magento-archived`) and verifies every image exists on the registry before `compose up`. **Create** rejects known-removed images immediately.

## Bind a hosted app

In `leco.yaml` or bridge manifest (future materialization):

```yaml
platform:
  devStackId: billing
```

LEco will inject stack connection env and optional network membership on deploy (see hosted app materialization).

## Connection strings

`GET /api/dev-stacks/<id>/snapshot` returns `connection_endpoints` with **host** (`127.0.0.1` when published) and **Docker DNS** (service names inside the stack).

## Component catalog

Defined in `ecosystem-stack/config/component-catalog.yaml` — SQL, cache, toolchains, mail, proxy components with version pins.
