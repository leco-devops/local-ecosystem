# Dev stack isolation

A **dev stack** is an isolated Docker Compose project (`leco-devstack-<id>`) with its own internal network. Use it when multiple apps on one VM need different **Node**, **Python**, or database versions without sharing one global MySQL/Node container.

## Architecture

- **Internal network:** `leco-devstack-<id>-internal` — databases and toolchains talk here.
- **Edge network:** `lh-network` — only HTTP services that Traefik should reach join this network.
- **No host ports** on databases by default — avoids port collisions on the VM.

## Create a stack

**Dashboard:** Platform → Dev stack builder → components like `postgres:16,redis:7,node:20`.

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

## Lifecycle

| Action | Effect |
|--------|--------|
| `start` | `docker compose -p leco-devstack-billing up -d` only |
| `stop` | Stops billing stack; other stacks keep running |
| `destroy` | `compose down -v --remove-orphans`, prunes leftover project containers/volumes/networks, removes `platform/dev-stacks/<id>/`, drops the stack from `config/leco-platform.yaml`, and regenerates `hosting/traefik/20-dev-stacks.yml` |

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
