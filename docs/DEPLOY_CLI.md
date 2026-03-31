# leco-app — deploy CLI

**leco-app** is a small command-line tool (under `tools/deploy-cli/`) that helps you deploy **third-party** applications in a **plug-and-play** way: one manifest per app, Docker Compose lifecycle, optional Cloudflare Wrangler deploy, and optional Traefik YAML fragments — without folding each app into the local-ecosystem `ai-stack` or `core.sh`.

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
leco-app init          # wizard: detects docker-compose + wrangler.toml
leco-app deploy
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
| Tool state | `~/.local/share/leco/apps/<name>/` |

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

Merge the output into `traefik/dynamic.yml` manually (backup first). Traefik watches the file; see [DEPLOY_CUSTOM_APPS.md](DEPLOY_CUSTOM_APPS.md).

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

## Ops dashboard vs `leco-app` apps

The **Ops Dashboard** **Control** tab lists **core** targets from `dashboard/control_targets.py` (AI stack, infra, Cloudflare-local, bulk actions). **Registered leco compose apps are not listed there** — use the **Hosted apps** tab instead.

**Hosted apps tab:** after you register an app (below), open **Hosted apps** in the dashboard for that stack’s **per-service metrics**, **CPU/memory/net history** (scoped to the compose project), **compose logs**, **insights** (restarts, simple CPU trend vs recent samples, optional HTTP probes to manifest `healthcheckUrls`), and the same **lifecycle controls** as before (`POST /api/control` with `target_id` **`leco-stack-<id>`**). APIs: `GET /api/hosted-apps`, `GET /api/hosted-apps/<slug>/snapshot`, `…/metrics/history`, `…/logs`, `…/insights`.

**Disable health URL probes** from the dashboard container (default on): set **`DASHBOARD_HOSTED_APP_HEALTH_PROBES=0`**.

**Register** third-party compose apps:

1. From your app directory (where `leco.app.yaml` lives):

   ```bash
   export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
   leco-app ecosystem-register
   ```

   Or: `leco-app ecosystem-register --ecosystem-root /path/to/local-ecosystem`

2. This creates/updates **`local-ecosystem/config/leco-registry.yaml`** with a manifest path **relative to the ecosystem repo** (e.g. `../CrawlerVision/leco.app.yaml` for a sibling checkout).

3. **`dashboard.sh`** mounts the ecosystem repo at **`/project`** and its **parent directory** at **`/workspace-parent`** (read-only) so those `../…` manifest paths work inside **`service-dashboard`**. If you start the dashboard some other way, set the same bind mount and **`DASHBOARD_WORKSPACE_PARENT=/workspace-parent`**, or keep manifests under the repo (e.g. symlink the app into `local-ecosystem/`).

4. **Rebuild/restart the dashboard** after code changes (`./ai-stack/ai-stack.sh deploy dashboard` or equivalent).

5. **Unregister:** `leco-app ecosystem-unregister crawlervision --ecosystem-root …`

**Overview** still shows all containers generically; the registry drives the **Hosted apps** tab and the same compose-backed **Control API** target ids.

When you **delete** containers with **`leco-app offload`** or **`docker compose down`**, they disappear from Docker on the next refresh; remove the registry entry with **`ecosystem-unregister`** so the Hosted apps entry goes away too.

## Offload — remove app from localhost

Tear down the compose stack and optionally strip this app’s routers/services from Traefik’s `dynamic.yml`:

```bash
cd /path/to/your/app
# Plan only
leco-app offload --dry-run --traefik-dynamic /path/to/local-ecosystem/traefik/dynamic.yml

# Execute: Traefik keys first (backup `.yml.bak`), then docker compose down
leco-app offload --traefik-dynamic /path/to/local-ecosystem/traefik/dynamic.yml

# Also remove compose volumes
leco-app offload -v --traefik-dynamic /path/to/local-ecosystem/traefik/dynamic.yml -y
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

## Relationship to local-ecosystem

| Component | Role |
|---------|------|
| `ai-stack`, Dashboard Control | **First-party** stacks only |
| **leco-app** | **External** repos: compose + wrangler + optional routing hints |

Full design notes and the **per-app vs shared platform** model: [tools/deploy-cli/README.md](../tools/deploy-cli/README.md).
