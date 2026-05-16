# Overriding upstream application behavior

LEco is designed so you **do not edit the upstream application repository** for local-ecosystem concerns. Overrides live in the **hosting slot** (`hosting/app-available/<slug>/`) and in generated overlays.

## Three override mechanisms

### 1. Hosting compose overlay (most common)

**File:** `docker-compose.leco-hosting.yml` beside `leco.app.yaml`

**Referenced from profile:**

```yaml
infrastructure:
  dockerCompose:
    composeFile: docker-compose.yml
    additionalComposeFilesFromManifest:
      - docker-compose.leco-hosting.yml
```

**Typical patches:**

- Attach all services to external network **`lh-network`**
- **`ports: !reset []`** — remove published host ports so Traefik is the only entry
- Set `*.lh` / same-origin env (e.g. clear hard-coded `REACT_APP_BACKEND_URL` pointing at production)

On **Register**, the dashboard runs **`ensure_lh_network_hosting_overlay`** — it can auto-create this file when missing.

**Samples:** `hosting/samples/sample-leco-hosting-overlay/`, `hosting/app-available/cvision/docker-compose.leco-hosting.yml`

### 2. Hosting-primary compose (`include` upstream)

**Field:** `infrastructure.dockerCompose.composeFileFromManifest`

Use a compose file **in the hosting tree** as the **first** `-f` file that `include`s upstream compose:

```yaml
# docker-compose.leco-entry.yml (in hosting slot)
include:
  - path: source/docker-compose.yml
services:
  web:
    ports: !reset []
    networks: [lh-network]
```

Upstream `source/docker-compose.yml` stays untouched.

### 3. Local edge runtime overlay

**Field:** `infrastructure.runtimes[]` (e.g. `type: cloudflare-workers`)

LEco generates **`docker-compose.leco-runtime.yml`**, sanitized **`wrangler.toml`** under `.leco-runtime/<id>/`, optional **`.dev.vars`** mount, and Traefik **`upstream`** rules to `leco-rt-<slug>-<runtime.id>`.

Use when production uses Workers/Pages but local Docker only serves part of the routes.

| Concern | Where it lives |
|---------|----------------|
| Sanitized wrangler | `hosting/app-available/<slug>/.leco-runtime/<id>/` |
| Secrets | `hosting/app-available/<slug>/.dev.vars` (gitignored) |
| Example secrets skeleton | `.dev.vars.example` (generated, never overwritten) |
| D1 bootstrap SQL | `.leco-runtime/<id>/d1-bootstrap-*.sql` |

On **Register**, **`ensure_local_runtime_overlay`** refreshes generated files.

## Routing overrides (Traefik)

Define **`infrastructure.routing.entries[]`** in **`leco.yaml`**:

```yaml
infrastructure:
  routing:
    entries:
      - hostname: myapp.lh
        upstream:
          - prefix: /api
            target: service
            service: api
          - prefix: /
            target: service
            service: web
```

Modern shape uses **`upstream[]`** with `target: service` or `target: runtime`. Legacy `frontend` / `apiBackend` / `backendHost` still work in older manifests.

Register with **`--merge-traefik`** writes routers/services into **`hosting/traefik/dynamic.yml`**. Stack routes (`ollama.lh`, `localhost.lh`, …) stay in **`traefik/dynamic.yml`** → copied to **`01-stack-core.yml`**.

## Lifecycle hooks (build/prepare without forking upstream)

**Profile `lifecycle`:**

```yaml
lifecycle:
  prepare:
    - run: npm ci
  build:
    - run: npm run build
  preStart:
    - run: ./scripts/wait-for-db.sh
```

Run manually:

```bash
leco-devops run-hooks -f hosting/app-available/myapp/leco.app.yaml --phase build
```

## Cloudflare-local (not Docker services in your compose)

**`infrastructure.cloudflare`** + **`provision-local-cf`** create KV/R2/D1 namespaces on shared adapters (`kv.lh`, `r2.lh`, `d1.lh`). Bindings in wrangler point at adapter URLs — LEco does **not** add six extra containers per binding to your app compose project.

Disable provision: `LECO_PROVISION_LOCAL_CF=0`.

## What LEco never does automatically

- Does not rewrite upstream source files on **Save YAML** (only hosting tree + symlinks).
- Does not auto-sync YAML when sibling repo changes — see [materialize](help:onboarding-materialize).
- Does not guess `dockerCompose` on every save — paths come from **`leco.yaml`** you configure.

## Operator checklist

1. Materialize or scaffold into `hosting/app-available/<slug>/`.
2. Add **`docker-compose.leco-hosting.yml`** or **`composeFileFromManifest`** pattern.
3. Set **`routing.entries`** for `*.lh` hostnames.
4. **Register** + **Deploy**.
5. If 502: verify **`lh-network`** and Traefik upstream — [502 / routing](help:ts-502).

## Maintainer reference

Full field list: **Docs** tab → *LECo App Blueprint*. Runbook: `docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md`.
