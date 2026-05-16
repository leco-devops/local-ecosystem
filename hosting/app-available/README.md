# Hosting materialization (`app-available`)

Each subdirectory holds a **bridge** (`leco.app.yaml`) + **profile** (`leco.yaml`) pair.

## Bridge vs profile (v3)

| File | Responsibility |
|------|----------------|
| **`leco.app.yaml`** | **Bridge to LEco:** `name` (registry slug), `root` (path resolution), `localHostProfile`, `applicationVersion`, optional `configRefs` (paths to `wrangler.toml`, compose, `.env`, Dockerfile, WordPress/nginx/Varnish/DB init, … — relative to **resolved root**), optional `localhost.notes` merged with the profile. Header comments in samples explain resolution, register/deploy, and Traefik. |
| **`leco.yaml`** | **Application + infra:** `infrastructure` (compose, cloudflare, routing, …), lifecycle, urls, archetype. |

`leco-devops` loads the bridge, merges `leco.yaml` `infrastructure` into the effective manifest, then runs compose / provision / Traefik as configured.

**Multiple compose files:** set **`infrastructure.dockerCompose.additionalComposeFiles`** in **`leco.yaml`** (list of paths relative to the resolved app root). `docker compose` is invoked with **`-f` primary `-f` each extra**; extra files must live **beside** the primary file in the real app tree (not only under `hosting/` unless you materialize or symlink them there). See **`docs/LECO_APP_BLUEPRINT.md`**.

## Reference samples

Manifest **samples** live in **`hosting/samples/`** (sibling of **`app-available/`**) so the Hosted apps tab does not list them as materialized staging apps. See **`../samples/README.md`**.

## Apps under this directory

| Folder | Purpose |
|--------|---------|
| **`cvision/`**, **`cloudflare/`**, … | Real or demo **materialized** slots (`leco.app.yaml` + profile, often with `source` → real repo). |

Provisioning for Workers still reads **`wrangler.toml`** via `infrastructure.cloudflare.wranglerConfig`. The **`wranglerBindingPreview`** block is informational unless refreshed by dashboard **Generate YAML** / **Save YAML**.
