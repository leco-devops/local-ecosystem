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
```

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
- **`additionalComposeFilesFromManifest`** — optional list merged **after** the above; paths are relative to **`leco.app.yaml`’s directory** (e.g. `hosting/app-available/myapp/docker-compose.leco-hosting.yml`). Keeps Traefik **`lh-network`** joins and public URL env overrides in the ecosystem repo while **`composeFile`** still points at the upstream project. Sample: **`hosting/samples/sample-leco-hosting-overlay/`**. For **split UI + `/api` routes**, frontends should call **`https://<slug>.lh/api/...`** (same origin), not `localhost:PORT` baked from upstream compose — the sample overlay sets **`REACT_APP_BACKEND_URL: ""`** and **`REACT_APP_SITE_URL`** (or your stack’s equivalent) so the browser uses the Traefik hostname.
- **`leco-app down`** exits **0** with a warning if the primary compose file is missing (treats the stack as already removed). LEco DevOps **Remove** still runs **full offboard** afterward.

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

## Offload — remove app from localhost

Tear down the compose stack and optionally strip this app’s routers/services from Traefik’s writable merge file (default **`hosting/traefik/dynamic.yml`**):

```bash
cd /path/to/your/app
# Plan only (default merge file when -E / LECO_ECOSYSTEM_ROOT is set)
leco-app offload --dry-run --traefik-dynamic /path/to/local-ecosystem/hosting/traefik/dynamic.yml

# Execute: Traefik keys first (backup `.yml.bak`), then docker compose down
leco-app offload --traefik-dynamic /path/to/local-ecosystem/hosting/traefik/dynamic.yml

# Also remove compose volumes
leco-app offload -v --traefik-dynamic /path/to/local-ecosystem/hosting/traefik/dynamic.yml -y
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

Compose-only offload (no Traefik file): `leco-app down` or `leco-app offload` without `--traefik-dynamic`.

**LEco DevOps — Hosted apps:** **Remove** / **Reset** and **`leco-app ecosystem-unregister`** (default) run **local CF cleanup** first (when enabled), then **`docker compose down`** when the manifest defines compose and the compose file exists, then Traefik strip and registry/hosting removal. Use **`--no-compose-down`** only if you must unregister without touching containers; **`--compose-volumes`** matches **`leco-app down -v`** (used by LEco DevOps **Reset**).

## Relationship to local-ecosystem

| Component | Role |
|---------|------|
| `ecosystem-stack`, LEco DevOps Control | **First-party** stacks only |
| **LEco DevOps** (`leco-app`) | **External** repos: compose + wrangler + optional routing hints |

Full design notes and the **per-app vs shared platform** model: [tools/deploy-cli/README.md](../tools/deploy-cli/README.md).
