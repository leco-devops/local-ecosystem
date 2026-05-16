# Hosted apps (dashboard & CLI)

Hosted apps are third-party applications registered in **`config/leco-registry.yaml`**, with manifests under **`hosting/app-available/<slug>/`**.

## Hosted apps tab

| Action | What it does |
|--------|----------------|
| **Register application** | Opens wizard: Detect → YAML → Register |
| **Deploy** | `docker compose up -d --build` for `leco-stack-<slug>` |
| **Logs / metrics** | Runtime snapshot, log tail, insights |
| **Attached services** | Per-app inventory: data stores, runtimes, CF bindings, credentials, **host vs Docker DNS** connection strings, management UIs |
| **Seed data** | Discover `data/` dumps; **Import data** / dry-run (NDJSON stream, reimport) — not run at register |
| **Remove / Reset** | Full unregister: CF teardown → compose down → Traefik strip → delete hosting slot |

**Pending registration:** folders in `app-available/` with valid YAML but no registry row yet — finish **Register**.

## Register (CLI)

```bash
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem

# Materialized slot
leco-devops ecosystem-register -E "$LECO_ECOSYSTEM_ROOT" \
  --registry-manifest-relpath hosting/app-available/myapp/leco.app.yaml \
  --merge-traefik

# From app directory after init
cd /path/to/myapp && leco-devops onboard -E "$LECO_ECOSYSTEM_ROOT"
```

## Control target

Each app becomes **`leco-stack-<id>`** on the **Control** tab (start, stop, down, deploy, staging, remove).

## Staging vs remove

| Term | Meaning |
|------|---------|
| **Staging / offload** | `leco-devops offload` or Control **staging** — compose down, strip Traefik keys, **keep** `hosting/app-available/<slug>/` |
| **Remove / unregister** | Deletes registry row and hosting slot (when manifest was under `hosting/`) |

There is **no** `hosting/app-staging/` directory.

## Zip upload

Dashboard or `POST /api/hosted/upload-zip` (control token) extracts into `hosting/app-available/<slug>/`, then **Detect** + **Register**.

## Deep dives

- [Hosting layout](help:hosting-layout)
- [Attached services panel](help:hosted-app-attached-services)
- [Seed data import](help:hosted-app-data-import)
- [Onboarding new apps](help:onboarding-overview)
- [wsp: & materialize](help:onboarding-materialize)
- [Overriding upstream apps](help:hosting-overrides)
- [Deploy & rebuild](help:deploy-rebuild)
- [Traefik routes](help:traefik-routes)

Back: [CLI basics](help:cli-basics)
