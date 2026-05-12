# LEco DevOps — deploy CLI reference

**LEco DevOps** is the product name for this tooling. The CLI is installed as **`leco-app`** and **`leco-devops`** (same program). It lives under `tools/deploy-cli/` and helps you deploy **third-party** applications: one manifest per app, Docker Compose lifecycle, optional Cloudflare Wrangler deploy, and optional Traefik YAML fragments — without folding each app into the local-ecosystem `ecosystem-stack` or `core.sh`.

For a task-oriented guide (registration wizard, workflows, troubleshooting), see **[LECO_USER_MANUAL.md](LECO_USER_MANUAL.md)** — it is also available in the LEco DevOps **Docs** tab. For how bridge + profile merge, **`hosting/`** symlinks, and teardown fit together, see **[LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md)**. For system context and component-level architecture, see **[ARCHITECTURE.md](ARCHITECTURE.md)**, **[HLD.md](HLD.md)**, **[LLD.md](LLD.md)**, and **[LECO_TOOLING.md](LECO_TOOLING.md)**.

## Install

**Use the `tools/deploy-cli/` directory** — it contains `pyproject.toml`. The parent **`tools/`** folder is only a container for tools; **`pip install -e .` from `tools/` fails** (no `setup.py` / `pyproject.toml` there).

From the **local-ecosystem repository root**:

```bash
cd tools/deploy-cli
pip install -e .
```

Equivalent absolute-style path:

```bash
cd /path/to/local-ecosystem/tools/deploy-cli
pip install -e .
```

Requires **Python 3.11+** and **Docker** with the Compose v2 plugin (`docker compose`).

## Usage summary

```bash
cd /path/to/application
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
leco-app onboard       # deploy + ecosystem-register + merge routing → hosting/traefik/dynamic.yml

leco-app init          # wizard: detects docker-compose + wrangler.toml; writes leco.yaml stub
leco-app init --onboard -E /path/to/local-ecosystem   # after deploy: register + Traefik merge
leco-app init --manifest-only   # minimal manifest + leco.yaml when you have no compose yet (confirm in TTY)
leco-app detect        # print JSON: compose files, wrangler, suggested archetype (for tooling / LEco DevOps)
leco-app run-hooks --phase prepare   # run lifecycle.prepare from merged sidecar profile
leco-app deploy
leco-app ecosystem-register --merge-traefik   # register only + Traefik merge (if already deployed)
# Optional logical registry path (same inode as --manifest): --registry-manifest-relpath hosting/app-available/myapp/leco.app.yaml
leco-app status
leco-app logs -f
leco-app down

leco-app runtimes                       # list adapters + declared runtimes + onboarding hint
leco-app runtimes --json --no-detect    # machine output, skip the Worker-path scan
```

**Runtime diagnostic.** `leco-app runtimes` is the canonical "did the local
edge runtime get wired up?" command. By default it:

- prints the registry (one row per adapter, ready vs roadmap),
- prints what your manifest declares under `infrastructure.runtimes[]`
  (per-runtime container name on `lh-network`, port, config, source dir),
- scans the resolved app root for a Worker entrypoint and surfaces a copy-
  pasteable `routing.entries[].upstream` YAML block matching the URL paths
  the Worker actually handles,
- enumerates **expected `.dev.vars` secrets** (every `env.<NAME>`
  referenced in Worker source that isn't already in wrangler.toml `[vars]`
  / bindings) and reports `wired: M/N (missing: …)` against the operator's
  actual `.dev.vars` file. Values are never logged — only key presence.

The same hints are emitted by the registration wizard (`leco-app
ecosystem-register` and the dashboard Register flow).

**Init — Traefik routes:** when the wizard asks for hostnames, **press Enter on an empty hostname** to finish adding routes (otherwise it keeps prompting). For **React + API** stacks, choose **split route** so the tool writes **`frontend`** + **`apiBackend`** and `traefik-fragment` emits **`Host && PathPrefix(/api…)`** routers (priority over the UI `Host` rule), matching how **local-ecosystem** routes apps like CrawlerVision.

**Compose:** attach the frontend and API containers to external network **`lh-network`** (see [DEPLOYMENT.md](DEPLOYMENT.md) section *Core infra — shell*) so Traefik can resolve them by container name.

From another directory, pass the manifest:

```bash
leco-app deploy --manifest /path/to/app/leco.app.yaml
```

Or search upward from `--cwd` for `leco.app.yaml`:

```bash
leco-app deploy --cwd /path/to/app
```

## What gets created

| Artifact | Location |
|----------|----------|
| Manifest | `leco.app.yaml` (default: application root) |
| Optional sidecar profile | **`leco.yaml`** (or legacy `localhost.yaml`; referenced by `localHostProfile`, or inline `localhost:`) |
| Tool state | `~/.local/share/leco/apps/<name>/` |

### Sidecar profile (`leco.yaml`)

Manifest **`lecoAppVersion: "2"`** can point at a sidecar file or embed the same shape inline:

```yaml
lecoAppVersion: "2"
localHostProfile: leco.yaml
# or: localhost: { schemaVersion: 1, archetype: node, ... }
```

### Manifest v3 (`lecoAppVersion: "3"`)

Recommended for new apps: keep **`leco.app.yaml`** as a **bridge** (`name`, `root`, `localHostProfile`, optional `configRefs`, `applicationVersion`, `localhost.notes`) and put **`infrastructure`** in **`leco.yaml`** — **`dockerCompose`** (including **`additionalComposeFiles`**), **`cloudflare`**, **`routing`**, health URLs, etc. The CLI and LEco DevOps load an **effective manifest** by merging profile `infrastructure` over the bridge (`tools/deploy-cli/leco_app/schema.py`).

- **`additionalComposeFiles`** — optional list of extra compose files; `docker compose` is run as **`-f` primary `-f` …** in list order. Paths are relative to the **resolved app root**; files must exist next to the primary compose file in the real checkout (not only under `hosting/` unless you symlink or copy them there).
- **`composeFileFromManifest`** — optional primary compose file **relative to `leco.app.yaml`’s directory** (e.g. `docker-compose.leco-entry.yml`). When set, it is the **first** **`-f`** and **`composeFile`** under the resolved app root is **not** used. Use to **`include`** upstream **`source/docker-compose.yml`** and patch services in the hosting tree only (e.g. **`ports: !reset []`** to stop publishing host **:80** when Traefik owns it, plus **`lh-network`**). Sample: **`hosting/samples/sample-hosting-compose-entry/`**.
- **`additionalComposeFilesFromManifest`** — optional list merged **after** the primary compose file (whether that primary is **`composeFile`** or **`composeFileFromManifest`**); paths are relative to **`leco.app.yaml`’s directory** (e.g. `hosting/app-available/myapp/docker-compose.leco-hosting.yml`). Keeps Traefik **`lh-network`** joins, host-port stripping (**`ports: !reset []`**), and public URL env overrides in the ecosystem repo while **`composeFile`** still points at the upstream project when you are **not** using **`composeFileFromManifest`**. Sample: **`hosting/samples/sample-leco-hosting-overlay/`**. For **split UI + `/api` routes**, frontends should call **`https://<slug>.lh/api/...`** (same origin), not `localhost:PORT` baked from upstream compose — the sample overlay sets **`REACT_APP_BACKEND_URL: ""`** and **`REACT_APP_SITE_URL`** (or your stack’s equivalent) so the browser uses the Traefik hostname. New hosted-app onboarding auto-generates this overlay when routing + compose are present.
- **`leco-app down`** exits **0** with a warning if the primary compose file is missing (treats the stack as already removed). LEco DevOps **Remove** still runs **full offboard** afterward.
- **`infrastructure.runtimes`** *(v3, optional)* — declares local edge-runtime containers (e.g. Cloudflare Workers run via Wrangler/Miniflare) LEco DevOps materializes into a generated **`docker-compose.leco-runtime.yml`** beside **`leco.app.yaml`**. Each entry has **`id`**, **`type`** (one of **`cloudflare-workers`**, **`cloudflare-pages`**, **`vercel`**, **`aws-lambda`**, **`deno-deploy`** — only **`cloudflare-workers`** is fully implemented in V1), and adapter-specific fields:
  - **`config`** — wrangler.toml (or equivalent) path relative to `sourceDir`.
  - **`sourceDir`** — relative to the manifest's resolved root.
  - **`port`** — container port Traefik forwards to (default `8787`).
  - **`devVarsFile`** — optional secrets file under `hosting/app-available/<slug>/`, bind-mounted read-only into the container at `/app/.dev.vars`. **Auto-detected** when the file is literally named `.dev.vars` and lives next to `leco.yaml`; the explicit field only matters for non-default paths.
  - **`stripBindings`** *(Cloudflare Workers only)* — list of top-level TOML tables to remove from a **sanitized** in-container `wrangler.toml` overlay; defaults to **`["browser"]`** (Browser Rendering — Miniflare cannot simulate it). Pass **`"none"`** to keep everything and rely on `wrangler dev --remote` instead. Upstream `wrangler.toml` is never edited on disk.
  - **`productionOnlyBindings`** *(Cloudflare Workers only, informational)* — list of bindings the production Worker uses that LEco cannot simulate locally (Browser Rendering, Vectorize, Hyperdrive, Analytics Engine, Email Routing producer, mTLS certs). Surfaced as `expected: production-only` badges in `leco-app runtimes` and the dashboard so operators don't chase phantom "down" markers in `/health` for paid CF features. Defaults to a conservative built-in list; set to `"none"` to suppress.
  - **`image`** — override the default runtime image (advanced; see `infra/runtimes/<type>/`).

  The runtime container's DNS name is **`leco-rt-<slug>-<runtime.id>`** on **`lh-network`**. The adapter also bind-mounts the per-runtime overlay directory **`hosting/app-available/<slug>/.leco-runtime/<runtime.id>/`** into the container at **`/leco-runtime/d1`**, where operators can drop **`d1-bootstrap-<BINDING>.sql`** schema files the runtime applies on first boot before running `wrangler d1 migrations apply` in a per-file-failure-tolerant loop. On every overlay materialization the adapter also scans the Worker source for `env.<UPPER_SNAKE>` references that are not declared in wrangler.toml `[vars]` or as bindings, and writes a **`.dev.vars.example`** skeleton (grouped by vendor) into `hosting/app-available/<slug>/`. Existing `.dev.vars.example` is never overwritten. See **`hosting/samples/sample-cf-worker-runtime/`** for the full shape and the runbook §7 for the failure modes this handles.
- **`routing.entries[].upstream`** *(v3, optional)* — list of **`{prefix, target, runtime?, service?}`** rules per hostname. **`target: runtime`** forwards a prefix to a sibling **`runtimes[].id`**; **`target: service`** (or alias **`frontend`** / **`backend`**) forwards to a Docker DNS name on **`lh-network`**. Replaces the legacy **`frontend`** / **`apiBackend`** / **`backendHost`** fields for the same entry; Traefik priority is derived from prefix length so **`/health/json`** outranks **`/api`** outranks **`/`** automatically (no manual priority math). The Cloudflare Workers adapter scans your Worker entrypoint (`src/index.ts`, `worker.ts`, …) for `pathname === '…'`, `pathname.startsWith('…')`, and router-call patterns (`app.get('…')`, etc.) and the registration wizard / **`leco-app runtimes --detect`** print a copy-pasteable YAML block listing the prefixes the Worker handles.

Full diagram and maintainer pointers: **[LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md)**.

**`leco.yaml` (v1)** holds operator metadata that does not replace Traefik `routing` by default:

| Field | Purpose |
|-------|---------|
| `schemaVersion` | `1` |
| `archetype` | Hint: `generic`, `wordpress`, `magento2`, `nextjs`, `node`, `php-fpm`, `laravel`, `static`, `java`, `dotnet` |
| `urls` | Logical endpoints (`role`, `label`, `publicUrl`, optional `internal`, `pathPrefix`) for docs and probes |
| `lifecycle` | `prepare` / `build` / `preStart` lists of `{ command, cwd?, shell?, timeoutSec? }` — run with **`leco-app run-hooks --phase …`** |
| `notes` | Free text |

**WordPress** — archetype + admin URL row:

```yaml
schemaVersion: 1
archetype: wordpress
urls:
  - role: frontend
    label: Site
    publicUrl: https://mysite.lh
  - role: admin
    label: WP admin
    publicUrl: https://mysite.lh/wp-admin
lifecycle:
  prepare: []
  build: []
  preStart: []
notes: ""
```

**Node / Next** — typical dev stack:

```yaml
schemaVersion: 1
archetype: nextjs
urls:
  - role: frontend
    label: App
    publicUrl: http://localhost:3000
lifecycle:
  prepare:
    - { command: "npm ci", shell: true, timeoutSec: 600 }
  build: []
  preStart: []
notes: ""
```

**Magento 2** — multi-URL table:

```yaml
schemaVersion: 1
archetype: magento2
urls:
  - role: frontend
    label: Storefront
    publicUrl: https://magento.lh
  - role: admin
    label: Admin
    publicUrl: https://magento.lh/admin
  - role: backend
    label: Internal PHP-FPM
    internal: http://php-fpm:9000
lifecycle:
  prepare: []
  build: []
  preStart: []
notes: ""
```

**Security:** `lifecycle` runs arbitrary commands — same trust model as `docker compose`; only use in repos you control. LEco DevOps can **register** apps (writes manifests + registry) only with the **control token**; paths must stay under the mounted project or `workspace-parent`.

## Cloudflare

If `wrangler.toml` or `cloudflare/wrangler.toml` is detected during `init`, the manifest can reference it.

```bash
leco-app cf-secrets-checklist --env staging
leco-app cf-deploy --env staging
leco-app cf-deploy --env production --confirm-production
```

Production deploys require **`--confirm-production`** to reduce accidents.

## Traefik (`*.lh`)

```bash
leco-app traefik-fragment -o /tmp/myapp-traefik.yml
```

Merge the output into **`hosting/traefik/dynamic.yml`** manually unless you pass **`--traefik-dynamic`** (a `.bak` copy is made; LEco DevOps uses an atomic replace). Traefik’s file provider **`watch: true`** reloads files under **`hosting/traefik/`** without restarting Traefik; restart the Traefik container after changing **`traefik/dynamic.yml`** (stack core) or static config / mounts. See [DEPLOY_CUSTOM_APPS.md](DEPLOY_CUSTOM_APPS.md).

The **LEco DevOps** web UI (container image built from the repo root) ships the **`leco-app`** / **`leco-devops`** CLI: **Hosted apps** control actions and **Register** use it; **Routes** can load **`traefik-fragment`** output by registry id and merge into **`hosting/traefik/dynamic.yml`**.

**Manifest — single backend (legacy):**

```yaml
routing:
  entries:
    - hostname: myapp.lh
      backendHost: my-nginx
      backendPort: 80
```

**Manifest — split UI + API (same hostname):**

```yaml
routing:
  entries:
    - hostname: myapp.lh
      apiPathPrefix: /api
      frontend:
        host: my-frontend
        port: 3000
      apiBackend:
        host: my-backend
        port: 8001
```

`leco-app traefik-fragment` turns that into four routers (HTTP/HTTPS × API/UI) with **priority 20** on the API path and **10** on the UI catch-all.

## LEco DevOps web UI vs registered apps

The **LEco DevOps** **Control** tab lists **core** targets from `dashboard/control_targets.py` (ecosystem stack, infra, Cloudflare-local, bulk actions). **Registered leco compose apps are not listed there** — use the **Hosted apps** tab instead.

**Hosted apps tab:** after you register an app (below), open **Hosted apps** in LEco DevOps for that stack’s **per-service metrics**, **CPU/memory/net history** (scoped to the compose project), **compose logs**, **insights** (restarts, simple CPU trend vs recent samples, optional HTTP probes to manifest `healthcheckUrls`), **local profile** (archetype, **`leco.yaml`** URLs and lifecycle summary), and the same **lifecycle controls** as before (`POST /api/control` with `target_id` **`leco-stack-<id>`**). APIs: `GET /api/hosted-apps`, `GET /api/hosted-apps/<slug>/snapshot`, `…/metrics/history`, `…/logs`, `…/insights`.

**Register from LEco DevOps:** expand **Register application** on the Hosted apps tab — **Detect** calls `POST /api/leco/detect` (path under `/project` or workspace-parent; optional `app_id` returns YAML previews). **Register** calls `POST /api/leco/register` with the **control token** and writes `leco.app.yaml`, **`leco.yaml`**, and updates `config/leco-registry.yaml`. Read-only **`wsp:`** paths are **materialized** under **`hosting/app-available/`** with a **`source`** symlink; the registry points to **`hosting/app-available/<slug>/leco.app.yaml`** (see **`hosting/README.md`** and **`docs/DEPLOYMENT.md`**). **`POST /api/hosted/upload-zip`** extracts a zip into **`hosting/app-available/<slug>/`** and removes the archive.

**Disable health URL probes** from the LEco DevOps container (default on): set **`DASHBOARD_HOSTED_APP_HEALTH_PROBES=0`**.

**Register** third-party compose apps:

1. From your app directory (where `leco.app.yaml` lives):

   ```bash
   export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
   leco-app ecosystem-register
   ```

   Or: `leco-app ecosystem-register --ecosystem-root /path/to/local-ecosystem`

2. This creates/updates **`local-ecosystem/config/leco-registry.yaml`** with a manifest path **relative to the ecosystem repo** (e.g. `../CrawlerVision/cloudflare/leco.app.yaml` for a sibling checkout).

3. **`dashboard.sh`** mounts the ecosystem repo at **`/project`** and its **parent directory** at **`/workspace-parent`** (read-only) so those `../…` manifest paths work inside **`service-dashboard`**. If you start LEco DevOps some other way, set the same bind mount and **`DASHBOARD_WORKSPACE_PARENT=/workspace-parent`**, or keep manifests under the repo (e.g. symlink the app into `local-ecosystem/`).

4. **Rebuild/restart LEco DevOps** after code changes (`./ecosystem-stack/ecosystem-stack.sh deploy dashboard` or equivalent).

5. **Unregister:** `leco-app ecosystem-unregister <registry-id> --ecosystem-root …`

**Overview** still shows all containers generically; the registry drives the **Hosted apps** tab and the same compose-backed **Control API** target ids.

When you **delete** containers with **`leco-app offload`** or **`docker compose down`**, they disappear from Docker on the next refresh; remove the registry entry with **`ecosystem-unregister`** so the Hosted apps entry goes away too.

## Offload — remove app from localhost (staging)

Tear down the compose stack and strip this app’s routers/services from Traefik’s writable merge file. Mirrors the dashboard **staging** button. Volumes are removed by default (`-v`); use `--no-volumes` to keep data.

Traefik `dynamic.yml` is **auto-detected** from `--ecosystem-root` / `LECO_ECOSYSTEM_ROOT` when `--traefik-dynamic` is not explicitly given:

```bash
cd /path/to/your/app
# Auto-detect Traefik dynamic.yml from ecosystem root:
leco-app offload -E /path/to/local-ecosystem

# Or with explicit Traefik path:
leco-app offload --traefik-dynamic /path/to/local-ecosystem/hosting/traefik/dynamic.yml

# Plan only (no file writes, no compose down):
leco-app offload --dry-run -E /path/to/local-ecosystem

# Keep volumes (don’t wipe data):
leco-app offload --no-volumes -E /path/to/local-ecosystem

# Skip confirmation:
leco-app offload -y -E /path/to/local-ecosystem
```

Traefik keys are derived from **`routing`** the same way as **`leco-app traefik-fragment`** (e.g. `myapp-myhost-lh-api-http`). If you **renamed keys** when merging into `dynamic.yml`, add explicit lists to the manifest:

```yaml
traefikCleanup:
  routers:
    - myapp-api-http
    - myapp-http
  services:
    - myapp-frontend-service
```

Compose-only offload (no Traefik file): `leco-app down` or `leco-app offload` without `-E` and without `--traefik-dynamic`.

**LEco DevOps — Hosted apps:** **Remove** / **Reset** and **`leco-app ecosystem-unregister`** (default) run **local CF cleanup** first (when enabled), then **`docker compose down`** when the manifest defines compose and the compose file exists, then Traefik strip and registry/hosting removal. Use **`--no-compose-down`** only if you must unregister without touching containers; **`--compose-volumes`** matches **`leco-app down -v`** (used by LEco DevOps **Reset**).

## Scaffold — generate app-available files from templates

The **`scaffold`** command copies a sample template to `hosting/app-available/<slug>/` and replaces all generic placeholders (`my-app`, `/path/to/your/app`, `app-network`, volume names) with your slug-specific values:

```bash
# Preview what would be created:
leco-app scaffold myapp -E /path/to/local-ecosystem --dry-run

# Create with all replacements:
leco-app scaffold myapp -E /path/to/local-ecosystem \
  --source-path /Users/you/GitHub/YourApp \
  --health-path /alb-health-check

# Use a different template:
leco-app scaffold myapp -E /path/to/local-ecosystem --template sample-compose-only
```

Available templates are listed in `hosting/samples/`. The default template is **`sample-node-varnish-multiprocess`** (multi-process Node.js + Varnish + MongoDB + Redis). After scaffolding, edit the generated files (especially `docker-compose.yml` source paths and `conf/varnish/default.vcl`), then register and deploy.

The `init` wizard also detects `conf/` directories, `leco-docker-preload.js`, and `docker-compose.leco-hosting.yml` hosting overlays — it will offer to add the overlay to `additionalComposeFilesFromManifest` automatically.

## Relationship to local-ecosystem

| Component | Role |
|---------|------|
| `ecosystem-stack`, LEco DevOps Control | **First-party** stacks only |
| **LEco DevOps** (`leco-app`) | **External** repos: compose + wrangler + optional routing hints |

Full design notes and the **per-app vs shared platform** model: [tools/deploy-cli/README.md](../tools/deploy-cli/README.md).
