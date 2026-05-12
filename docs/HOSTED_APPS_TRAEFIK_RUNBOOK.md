# Hosted apps — Traefik & `*.lh` runbook

This document records **common failures** seen with **LEco DevOps Hosted apps** (compose stacks behind Traefik), **root causes**, and **fixes** implemented in this repository. Use it with **[DEPLOY_CLI.md](DEPLOY_CLI.md)**, **[LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md)**, and **[DEPLOYMENT.md](DEPLOYMENT.md)** §12.

---

## 1. Symptom → cause → fix (summary)

| Symptom | Typical cause | Fix in repo / operator action |
|--------|----------------|------------------------------|
| **502** from Traefik; compose `ps` healthy | App containers not on **`lh-network`** (Traefik is on that network only) | Add **`hosting/app-available/<slug>/docker-compose.leco-hosting.yml`** joining **`lh-network`**, list it under **`infrastructure.dockerCompose.additionalComposeFilesFromManifest`**, **redeploy** compose. **`ensure_lh_network_hosting_overlay()`** in `dashboard/leco_detect.py` (Register / Validate) can create the file and profile entry when routing + compose are present, and now also strips upstream host `ports:` with **`!reset`** for new overlays. |
| **502**; wrong upstream name | Traefik **`loadBalancer`** URL does not match **Docker DNS** for that service | Use **`container_name`** from the compose file if set; otherwise default **`{project}-{service}-1`** (Compose v2+). **`_compose_service_backend_host()`** in `dashboard/leco_detect.py` encodes this. **`normalize_profile_compose_backend_hosts()`** rewrites ambiguous hosts. |
| **502** after renaming registry id | **`routing.entries`** still use another app’s prefix (e.g. **`cv-frontend`** while **`projectName`** is **`cvision`**) | **`_remap_stale_compose_dns_host()`** / **`_compose_service_key_from_routing_host()`** remap wrong-prefix hosts to the canonical name for **`dockerCompose.projectName`**. Run **Validate** or **Register** to persist. |
| UI **OK** but dashboard shows **“HTTP 0”** or bogus API status for **`https://*.lh`** | URL probes used **`https://traefik/...`** internally; Traefik is reached on **HTTP :80** inside Docker | **`get_probe_target()`** in `dashboard/monitor.py` now uses **`http://traefik{path}`** with **`Host: <app>.lh`** for all `*.lh` URLs (http and https). **Restart LEco DevOps** after upgrading. |
| SPA calls **`http://localhost:8001/api`** while opened on **`https://<app>.lh`** | Upstream **`docker-compose.yml`** sets **`REACT_APP_BACKEND_URL=http://localhost:8001`** (wins over same-origin logic) | Hosting overlay sets **`REACT_APP_BACKEND_URL: ""`** and **`REACT_APP_SITE_URL: https://<app>.lh`** (see **`_lh_overlay_env_for_service()`** in `dashboard/leco_detect.py` and **`hosting/samples/sample-leco-hosting-overlay/`**). **CrawlerVision** also prefers **`window.location.origin`** on **`*.lh`** in **`frontend/src/config/api.js`** (upstream repo). |
| Dashboard detail panel shows **"main URL 502"** but per-URL probes return **200** | Detail view reads `main_url_probe` from the **cached sidebar list** (`/api/hosted-apps`), which can be stale (90 s client cache + localStorage). The fresh per-app snapshot (`url_probes`) is ignored. | `dashboard/static/dashboard.js` now prefers the fresh `url_probes[main_url]` from the snapshot over the cached `app.main_url_probe` when rendering the detail status dot. |
| Register wizard: changing **App id (slug)** does not update Public URL hostnames | URLs were populated by **Detect** (which derives the hostname from the folder name, e.g. `cloudflare.lh`), not from the slug. No input listener synced them. | `dashboard/static/dashboard.js` adds an `input` listener on the slug field that reads the *actual* hostname from the first URL row and rewrites all rows when the slug changes (preserving custom paths like `/api`, `/graphql`). |
| **`Bind for 0.0.0.0:80` / `:3000` / `:5432` / … **`port is already allocated`** on **`docker compose up`** | Upstream compose publishes **host ports** (web **80/3000**, API **8001**, Postgres **5432**, etc.) that another process or stack already uses | Keep the upstream repo untouched: add **`ports: !reset []`** in the hosting overlay (**`docker-compose.leco-hosting.yml`**) for every publishing service. New hosted-app onboarding now auto-generates this in **`ensure_lh_network_hosting_overlay()`**. If you want the hosting tree to own the **primary** compose file instead of an extra overlay, use **`docker-compose.leco-entry.yml`** with **`composeFileFromManifest`** + **`include`** (see **`hosting/samples/sample-hosting-compose-entry/`**). Unset env warnings → add **`envFile`** under hosting or a **`.env`** beside the manifest. |
| **`/api/geo/ip-country`** **404** in Docker but works on Cloudflare | Route existed only on the **Worker** (`request.cf.country`), not in **FastAPI** | **CrawlerVision** **`backend/server.py`** adds **`GET /api/geo/ip-country`** for Docker (headers, **`GEO_IP_DEV_COUNTRY`**, optional **ip-api** lookup). |
| **Worker-only API** routes 404 on **`<slug>.lh/api/*`** even though they exist in **`cloudflare/`** / **`functions/`** / etc. — production serves them fine | Local routing pointed **`/api`** at a classic compose backend (e.g. FastAPI) which only implements a subset of the production routes — the rest live in the edge runtime that isn't running locally | Declare **`infrastructure.runtimes[]`** in **`leco.yaml`** with **`type: cloudflare-workers`** (or **`vercel`** / **`aws-lambda`** / **`deno-deploy`** / **`cloudflare-pages`** once their adapters land) and switch **`routing.entries[].upstream[]`** to forward **`/api`** at **`target: runtime`**. See **§7 Local edge runtimes**, **`hosting/samples/sample-cf-worker-runtime/`**, and run **`leco-app runtimes -f leco.app.yaml`** to confirm. |
| **502** on **`<slug>.lh/api/*`** after switching to **`target: runtime`**; runtime container in **`Restarting`**; logs show *"Browser Rendering is not supported locally"* | Upstream **`wrangler.toml`** declares **`[browser]`** (or another binding Miniflare cannot simulate). Wrangler local exits before binding **`:8787`**, so Traefik reports **502**. | The Cloudflare Workers adapter auto-strips **`[browser]`** by default into a sanitized **`hosting/app-available/<slug>/.leco-runtime/<runtime_id>/wrangler.toml`** and bind-mounts it on top of **`/app/wrangler.toml`** *inside the runtime container only* — upstream tree untouched. Override the strip list via **`infrastructure.runtimes[].stripBindings`** (list, or **`"none"`** to disable). See **§7 → Locally-unsupported bindings**. |
| **502** on **`<slug>.lh/api/*`**; runtime logs show **`spawn …/workerd ENOENT`** | LEco runtime image was on **`node:22-alpine`** (musl libc). Cloudflare's **`workerd`** binary published on npm is glibc-only and crash-loops on musl. | Image is now **`node:22-bookworm-slim`** (glibc). Rebuild **`leco/runtime-cloudflare-workers:latest`** from **`infra/runtimes/cloudflare-workers/`** and recreate the runtime container *and* its per-app **`node_modules`** named volume (musl-built artifacts must be evicted): `docker volume rm <project>_leco-rt-<slug>-<runtime>-node-modules` then **`leco-app deploy`**. |
| **500** with **`D1_ERROR: no such table: <name>: SQLITE_ERROR`** on a Worker route | Local D1 file is empty — production was bootstrapped out-of-band (e.g. a `D1_INIT_SQL` exec from an admin endpoint) and/or **`wrangler d1 migrations apply`** aborted on a single bad migration leaving later ones pending. | Drop the upstream baseline schema into **`hosting/app-available/<slug>/.leco-runtime/<runtime_id>/d1-bootstrap-<BINDING>.sql`** (or **`d1-bootstrap.sql`** as a fallback) and redeploy. The runtime entrypoint applies the bootstrap, then runs a **resilient** migration loop that tolerates per-file failures by recording them in `d1_migrations` and retrying. See **§7 → D1 schema bootstrap + resilient migrations**. |
| **`<slug>.lh/<path>`** returns the **frontend SPA shell** (HTML with *"You need to enable JavaScript to run this app."*) but production returns JSON for the same path | Path is Worker-served in production but no **`routing.entries[].upstream`** rule directs it at the local runtime — Traefik falls through to the catch-all **`/`** → frontend service. Common offenders: **`/health/json`**, **`/.well-known/*`**, **`/metrics`**, **`/sitemap.xml`**. | Add a longer-prefix rule pointing at the runtime in **`leco.yaml`**, e.g. **`{ prefix: /health/json, target: runtime, runtime: worker }`**. The Cloudflare Workers adapter's **`detect()`** now scans the Worker entrypoint (**`src/index.ts`** / **`worker.ts`**) for `pathname === '...'` / `pathname.startsWith('...')` / `app.get('...', …)` patterns and surfaces a hint with suggested rules during onboarding. Re-merge Traefik after editing (**`leco-app ecosystem-register --merge-traefik`**); the longer prefix automatically wins because the fragment generator derives router priorities from prefix length. |
| **Worker `/health/json`** (or any feature board) reports many SaaS / LLM / payment / email services as **`down`** locally while production shows them healthy | The Worker references `env.OPENAI_API_KEY`, `env.STRIPE_SECRET_KEY`, etc., but no `.dev.vars` is wired into the runtime container, so the Worker's `hasEnvVar()` checks return `false`. LEco is healthy — those are operator-supplied secrets that ship as Wrangler secrets in production. | LEco now auto-generates **`hosting/app-available/<slug>/.dev.vars.example`** on every overlay materialization, listing every UPPER_SNAKE `env.<NAME>` referenced in Worker source that is NOT already declared in wrangler.toml `[vars]` or as a binding (grouped by vendor: LLM / Payments / Email / Cloudflare / Other). Copy it to `.dev.vars`, fill in real values, redeploy. The runtime adapter auto-bind-mounts `.dev.vars` at `/app/.dev.vars` whenever the file exists — no manifest field required. Run **`leco-app runtimes -f leco.app.yaml`** to see `wired: N/M (missing: …)` against the operator file. See **§7 → Operator-supplied secrets (`.dev.vars` auto-scan)**. |
| **Worker `/health/json`** still reports `browser` / `vectorize` / `analytics_engine_datasets` as **`down`** locally after secrets are filled in | Those bindings are *production-only* Cloudflare features (Browser Rendering, Vector embeddings store, Analytics Engine — none have a Miniflare equivalent). They will *always* be `down` locally. | Declare them under **`infrastructure.runtimes[].productionOnlyBindings`** in **`leco.yaml`** (e.g. `[browser, vectorize, analytics_engine_datasets, send_email]`). LEco surfaces them as `expected: production-only` informational badges so operators don't chase phantom red dots. Defaults to a conservative built-in list; set to `none` to suppress entirely. |

---

## 2. Correct routing shape (split UI + API)

For **React + API** on one hostname:

- **`leco.yaml`** → **`infrastructure.routing.entries`**: **`hostname`**, **`apiPathPrefix`** (often **`/api`**), **`frontend`**, **`apiBackend`** (host + port).
- **`leco-app traefik-fragment`** / merge produces **higher-priority** routers for **`Host(...) && PathPrefix(/api)`** → API container, and **lower-priority** **`Host(...)`** → UI container (`tools/deploy-cli/leco_app/traefik_fragment.py`).
- Traefik forwards the **full path** (including **`/api`**) to the backend; the API must mount routes under **`/api`** (same as direct `localhost:8001` usage).

---

## 3. Operator checklist (new or broken app)

1. **`dockerCompose.projectName`** matches **`docker compose -p`** (stable DNS prefix when **`container_name`** is absent).
2. **`routing.entries`** upstream hosts match **`container_name`** or **`{project}-{service}-1`**.
3. **`additionalComposeFilesFromManifest`** includes **`docker-compose.leco-hosting.yml`** (or equivalent) so **frontend** and **api** services join **`lh-network`**.
4. The hosting overlay clears upstream host publishes with **`ports: !reset []`** for any service that exposed ports in the upstream compose.
5. **`docker compose … up -d`** uses **both** the upstream **`-f`** and the hosting overlay **`-f`** (LEco DevOps deploy does this when the profile is correct).
6. **`hosting/traefik/dynamic.yml`** merged (Register / **`leco-app ecosystem-register --merge-traefik`**).
7. For **same-origin API** on `*.lh`, overlay env clears hardcoded **`localhost`** API base vars (framework-specific).
8. After changing **dashboard** probe logic, **restart** the **`service-dashboard`** container.

### 3a. Automated onboarding pipeline (what LEco does for you)

The dashboard **Register** flow and **`leco-app ecosystem-register`** run the
steps below in order. When an app behaves like a "production-faithful" Worker
stack (CV is the reference), none of these need operator intervention beyond
declaring **`infrastructure.runtimes[]`** in **`leco.yaml`**.

1. **`ensure_lh_network_hosting_overlay`** *(always)* — writes / refreshes
   **`hosting/app-available/<slug>/docker-compose.leco-hosting.yml`**: forces
   frontend / api services onto **`lh-network`**, applies **`ports: !reset []`**
   so upstream host port publishes do not collide with LEco core, sets
   **`REACT_APP_BACKEND_URL=""`** / **`*_SITE_URL=https://<slug>.lh`** defaults.

2. **Local edge-runtime detection** *(when an adapter recognises the source)*
   — **`dashboard/leco_detect.py::detect_runtime_candidates_for_manifest`**
   walks the resolved source root, scoring **`wrangler.toml`** candidates by
   sibling **`src/`** content so multi-project repos (root stub + real
   **`cloudflare/wrangler.toml`**) pick the right one. The wizard log surfaces
   the detected runtime, the URL paths the Worker entrypoint actually handles,
   and a **copy-pasteable** **`routing.entries[].upstream`** YAML block. The
   same hint is emitted by **`leco-app runtimes`** (default behavior;
   **`--no-detect`** to skip).

3. **`ensure_local_runtime_overlay`** *(only if `infrastructure.runtimes[]` declared)*
   — materializes **`docker-compose.leco-runtime.yml`** beside the manifest
   with one service per runtime (DNS: **`leco-rt-<slug>-<runtime.id>`**).
   Bind-mounts the upstream source at **`/app`**, masks **`/app/node_modules`**
   and **`/app/.wrangler`** with LEco-owned named volumes. The Cloudflare
   Workers adapter additionally:
   - writes a **sanitized `wrangler.toml`** under
     **`hosting/app-available/<slug>/.leco-runtime/<rid>/wrangler.toml`**
     (stripping **`[browser]`** by default plus any extras the operator listed
     in **`stripBindings`**) and bind-overlays it on top of
     **`/app/wrangler.toml`** in the container only — upstream
     **`wrangler.toml`** is never edited;
   - bind-mounts the per-runtime overlay directory at **`/leco-runtime/d1`** so
     operators can drop **`d1-bootstrap-<BINDING>.sql`** files the runtime
     applies once on first boot before migrations run.

4. **`leco-app ecosystem-register --merge-traefik`** *(always)* — reads the
   effective **`routing.entries[].upstream[]`**, emits Traefik routers via
   **`traefik_fragment.py::_upstream_routing_fragment`** (priority = prefix
   length so **`/health/json`** outranks **`/api`** outranks **`/`** without
   manual priority math), and merges them into
   **`hosting/traefik/dynamic.yml`** atomically (**`.bak`** written first).

5. **`leco-app deploy`** *(triggered by the wizard or the Hosted apps card)*
   — **`docker compose -f <upstream> -f leco-hosting -f leco-runtime up -d`**.
   On first boot the runtime entrypoint applies bootstrap SQL, then loops
   **`wrangler d1 migrations apply`** with per-file failure tolerance, then
   execs **`wrangler dev --local --persist-to .wrangler/state`**.

End state: every URL the production Worker handles is handled locally on
**`<slug>.lh`**; every URL it doesn't handle 404s locally too. No drift, no
upstream patches.

---

## 4. Code map (automation & UI)

| Concern | Location |
|--------|----------|
| Compose DNS / normalization / wrong-prefix remap | `dashboard/leco_detect.py` (`_compose_service_backend_host`, `_normalize_compose_routing_backend_hosts`, `_remap_stale_compose_dns_host`, `ensure_lh_network_hosting_overlay`) |
| Register / Validate auto-heal | `dashboard/leco_registration.py`, `dashboard/leco_validate.py` |
| `*.lh` URL probes | `dashboard/hosted_apps.py` (`_probe_main_url`, `_probe_url_map`), `dashboard/monitor.py` (`get_probe_target`, `check_url`) |
| Fresh probe override in detail panel | `dashboard/static/dashboard.js` — snapshot `url_probes` → `app.main_url_probe` before `hostedMainUrlProbeSummary()` |
| Slug → URL auto-sync (register wizard) | `dashboard/static/dashboard.js` — `_extractHostFromUrlRows()`, `idIn` input listener |
| Compose `-f` chain | `tools/deploy-cli/leco_app/compose_runner.py` |
| Traefik fragment | `tools/deploy-cli/leco_app/traefik_fragment.py` |
| Example overlay | `hosting/samples/sample-leco-hosting-overlay/`, `hosting/app-available/cvision/docker-compose.leco-hosting.yml` |

---

## 5. Reference apps in this repo

- **`hosting/app-available/cvision/`** — bridge + profile + **`docker-compose.leco-hosting.yml`** for **CrawlerVision**-style **frontend** / **backend** + **`cv-frontend`** / **`cv-backend`** **`container_name`** in upstream compose (Traefik targets **`cv-frontend`**, **`cv-backend`**, not **`cvision-frontend-1`**, when those names are set).

---

## 6. Related documentation

- **[DEPLOY_CLI.md](DEPLOY_CLI.md)** — `additionalComposeFilesFromManifest`, split routes, same-origin **`/api`** note, **`runtimes[]`** + **`upstream[]`** reference.
- **[LECO_USER_MANUAL.md](LECO_USER_MANUAL.md)** — Hosted apps tab, troubleshooting.
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — §12 Troubleshooting, **`repair-network`**.

---

## 7. Local edge runtimes (Workers, Pages, Vercel, Lambda, Deno)

Modern apps split traffic between a classic backend (compose) **and** an edge
runtime (Cloudflare Workers, Vercel Functions, Lambda, etc.). LEco DevOps runs
the edge runtime locally so **`<slug>.lh/api/*`** matches production faithfully —
including its 404s. No upstream changes; LEco-owned volumes mask
**`node_modules`** / **`.wrangler`** so the upstream repo is never written to.

### Manifest shape

```yaml
# hosting/app-available/<slug>/leco.yaml
infrastructure:
  runtimes:
  - id: worker
    type: cloudflare-workers   # adapter selector (see table below)
    config: wrangler.toml      # relative to manifest root / sourceDir
    sourceDir: cloudflare      # optional; relative to manifest resolved root
    port: 8787                 # container port Traefik forwards to
    # devVarsFile: .dev.vars   # optional, under hosting/app-available/<slug>/
  routing:
    entries:
    - hostname: <slug>.lh
      upstream:
      - prefix: /api
        target: runtime
        runtime: worker
      - prefix: /
        target: service
        service: { host: <slug>-frontend, port: 3000 }
```

LEco then materializes **`docker-compose.leco-runtime.yml`** beside
**`leco.app.yaml`** (one service per runtime), appends it to
**`additionalComposeFilesFromManifest`**, and emits Traefik routers with
prefix-length-derived priorities (longest prefix wins, no overlap drift).

### Adapter status (V1)

| `type:` | Status | Image | Notes |
|---------|--------|-------|-------|
| **`cloudflare-workers`** | **Ready** | `leco/runtime-cloudflare-workers:latest` | `wrangler dev --local --persist-to .wrangler/state`. Image source under **`infra/runtimes/cloudflare-workers/`**. Base is **`node:22-bookworm-slim`** (glibc); Cloudflare's `workerd` binary is glibc-only and crash-loops on Alpine/musl. The entrypoint prefers the upstream's locally-installed wrangler (`/app/node_modules/.bin/wrangler`) so each app pins its own version + matching `workerd`. |
| `cloudflare-pages` | Roadmap | `leco/runtime-cloudflare-pages` | Will wrap `wrangler pages dev`. |
| `vercel` | Roadmap | `leco/runtime-vercel` | Will wrap `vercel dev`. |
| `aws-lambda` | Roadmap | `leco/runtime-aws-lambda` | SAM CLI / LocalStack. |
| `deno-deploy` | Roadmap | `leco/runtime-deno-deploy` | `deno serve`. |

Roadmap types are valid in the schema today; declaring them logs a
"roadmap — overlay skipped" note until the adapter ships. Operators can lay
out manifests in advance.

### D1 schema bootstrap + resilient migrations

Cloudflare's **D1** is just a SQLite file locally. The runtime creates it
empty — but the upstream app may expect a populated schema, either because
production was bootstrapped out-of-band (e.g. a `D1_INIT_SQL` constant the
Worker `exec()`s from an admin endpoint) or because the `migrations/`
directory only covers diffs *on top of* such a baseline.

When the Cloudflare Workers adapter materializes its compose service it
bind-mounts the per-app directory **`hosting/app-available/<slug>/.leco-runtime/<runtime_id>/`** into the container at **`/leco-runtime/d1`** (read-only)
and sets **`LECO_D1_BOOTSTRAP_DIR=/leco-runtime/d1`**. The entrypoint then
runs two phases for every top-level **`[[d1_databases]]`** binding it finds
in the (sanitized) wrangler.toml:

1. **Bootstrap (first-boot, idempotent).** If the directory contains any of:

   - **`d1-bootstrap-<BINDING>.sql`** (preferred, case-sensitive)
   - **`d1-bootstrap-<binding-lower>.sql`**
   - **`d1-bootstrap.sql`** (global fallback)

   …the entrypoint runs **`wrangler d1 execute <BINDING> --local --file <path>`**
   before any migrations. A sentinel
   **`.wrangler/state/leco-d1-bootstrap-<BINDING>.applied`** prevents
   re-application on subsequent boots; set **`LECO_APPLY_D1_BOOTSTRAP=always`**
   to force, **`=off`** to skip entirely. Bootstrap SQL is operator-owned —
   use **`CREATE TABLE IF NOT EXISTS`** / **`CREATE INDEX IF NOT EXISTS`** /
   **`INSERT OR IGNORE`** so re-apply is a no-op.

2. **Resilient migrations.** **`wrangler d1 migrations apply <BINDING> --local`** is normally all-or-nothing: one failing migration (e.g. an
   **`ALTER TABLE`** whose column already exists from the bootstrap) aborts
   the whole batch and leaves later migrations the operator needed pending.
   The entrypoint wraps the apply in a bounded loop (default **50** iter):
   on failure it parses the failed file out of wrangler's stderr, records
   it in **`d1_migrations`** as "applied" (effectively a local-only skip),
   and retries. Each retry advances past the previously-failed file until
   the batch reports success or the loop stalls. The trade-off is explicit:
   migrations marked as skipped locally do **not** create whatever tables
   they intended — that's the operator's bootstrap-SQL responsibility.

   Override via **`LECO_APPLY_D1_MIGRATIONS=off`** (skip), **`auto`** (apply
   when **`migrations/`** exists — default), or **`always`** (force even
   without a migrations dir).

**Generating the bootstrap SQL.** When upstream has the schema in a
TypeScript constant (CrawlerVision's **`D1_INIT_SQL`** in
**`src/services/db-init.ts`** is the reference case), extract it once into
the per-app **`.leco-runtime/<runtime_id>/`** dir — the file is gitignored
via the parent **`hosting/app-available/*/`** rule, so it stays per-machine
and can include local seed data. Re-extract when upstream changes.

### Locally-unsupported bindings (auto-stripped)

Some Cloudflare Workers bindings make `wrangler dev --local` exit immediately
because Miniflare cannot simulate them. The Cloudflare Workers adapter
materializes a *sanitized* copy of the upstream `wrangler.toml` under
`hosting/app-available/<slug>/.leco-runtime/<runtime_id>/wrangler.toml` and
bind-mounts that file on top of `/app/<config>` **inside the runtime container
only**. The upstream `wrangler.toml` on disk is never edited.

Stripped by default: **`[browser]`** (Cloudflare Browser Rendering — local mode
hard-errors with *"Browser Rendering is not supported locally. Please use
`wrangler dev --remote` instead."*).

Override per-app via `infrastructure.runtimes[].stripBindings` — either an
explicit list (`["browser", "ai"]`) or `"none"` / `[]` to disable stripping
entirely (use `--remote` mode if you need full fidelity). The sanitized file
includes a header listing which sections were dropped.

### Operator-supplied secrets (`.dev.vars` auto-scan)

Production Workers usually rely on a handful of operator-supplied secrets
(`OPENAI_API_KEY`, `STRIPE_SECRET_KEY`, `BREVO_SMTP_KEY`, `CF_API_TOKEN`,
`TURNSTILE_SECRET_KEY`, …) that ship through `wrangler secret put` — never
into `wrangler.toml`. Without them every feature that depends on those keys
reports `down` in the Worker's health board even though LEco itself is
healthy. LEco closes this discoverability gap automatically:

1. **Auto-scan.** The Cloudflare Workers adapter walks the Worker source
   (`src/**/*.ts|tsx|js|mjs|cjs`, bounded scan) for `env.<UPPER_SNAKE>` and
   `env["UPPER_SNAKE"]` references, then subtracts every name already
   declared in `wrangler.toml` `[vars]` or as a `binding = "NAME"` (anywhere,
   including the inline `assets = { …, binding = "ASSETS" }` form). The
   remainder is the *expected secrets* set.

2. **Auto-generated `.dev.vars.example`.** Every overlay materialization
   writes `hosting/app-available/<slug>/.dev.vars.example` (gitignored)
   listing one `KEY=` placeholder per expected secret, **grouped by vendor**
   (Cloudflare platform / LLM providers / Payments / Email / Other) so an
   operator can fill values without spelunking through the upstream source.
   Existing `.dev.vars.example` is **never overwritten** — operators can
   safely add comments / re-order.

3. **Auto-bind `.dev.vars`.** If `hosting/app-available/<slug>/.dev.vars`
   exists, the adapter bind-mounts it at `/app/.dev.vars` inside the runtime
   container. Wrangler reads it automatically. No manifest field required;
   `infrastructure.runtimes[].devVarsFile:` is still accepted for explicit
   configuration (e.g. pointing at a non-default path).

4. **Diagnostic surface.** Both the registration wizard log and
   `leco-app runtimes -f leco.app.yaml` print
   `expected .dev.vars secrets: N (wired: M, missing: …)` with the actual
   missing key names, plus the path of `.dev.vars` (when present) or the
   skeleton file (when not). Values are never logged — only key presence.

The flow for any new Worker app:
```bash
# 1. Register the app — LEco scans the source and writes .dev.vars.example.
leco-app ecosystem-register --merge-traefik

# 2. Copy the skeleton and fill in real secret values.
cp hosting/app-available/<slug>/.dev.vars.example \
   hosting/app-available/<slug>/.dev.vars
${EDITOR} hosting/app-available/<slug>/.dev.vars

# 3. Redeploy — the adapter auto-mounts .dev.vars; Wrangler picks it up.
leco-app deploy
```

### Production-only bindings (informational badge)

Some Cloudflare-platform features genuinely have no local equivalent:
**`browser`** (Browser Rendering), **`vectorize`** (Vector store),
**`hyperdrive`** (managed-DB connection pool),
**`analytics_engine_datasets`** (Analytics Engine), **`send_email`**
(Email Routing producer), **`mtls_certificates`**. Even after every secret
is filled in, the Worker's `/health/json` will keep reporting these as
`down` because Miniflare returns `null`/`undefined` for them.

Declare them under `infrastructure.runtimes[].productionOnlyBindings` in
`leco.yaml`:

```yaml
infrastructure:
  runtimes:
  - id: worker
    type: cloudflare-workers
    productionOnlyBindings: [browser, vectorize, analytics_engine_datasets, send_email]
```

LEco surfaces those as `expected: production-only` informational badges in
`leco-app runtimes` output and the dashboard hosted-app card so operators
can tell at a glance which red dots are "missing API key" (actionable) vs
"paid CF feature, will-never-work-locally" (expected). Defaults to a
conservative built-in list — override with a custom list or set to
`"none"` to suppress the badge.

### Diagnostic command

`leco-app runtimes -f leco.app.yaml` prints the adapter registry, the
declared runtimes for that manifest, the detection hint (Worker paths +
suggested `routing.upstream` YAML), and the secret wiring snapshot
(expected / wired / missing). Combine with
`docker logs leco-rt-<slug>-<runtime>` to confirm the runtime is up.

### Code map

| Concern | Location |
|---------|----------|
| Schema (`LocalRuntimeSpec`, `RoutingUpstreamRule`) | `tools/deploy-cli/leco_app/schema.py` |
| Adapter registry + Cloudflare Workers impl + 4 stubs | `dashboard/leco_runtimes/` |
| Overlay materialization (`ensure_local_runtime_overlay`) | `dashboard/leco_detect.py` |
| Wizard hook (auto-detect `wrangler.toml`, log materialization) | `dashboard/leco_registration.py::iterate_register_app_wizard` |
| Traefik routers (priority by prefix length) | `tools/deploy-cli/leco_app/traefik_fragment.py::_upstream_routing_fragment` |
| Cloudflare Workers reference image | `infra/runtimes/cloudflare-workers/` |
| Sample manifest pair | `hosting/samples/sample-cf-worker-runtime/` |
| CLI: `leco-app runtimes` | `tools/deploy-cli/leco_app/cli.py::cmd_runtimes` |

### CV opted-in (reference)

`hosting/app-available/cv/leco.yaml` switched to the upstream-driven routing
shape with **`infrastructure.runtimes[id=worker, type=cloudflare-workers]`**
and **`upstream: [/api → runtime, / → cv-frontend:3000]`**. With this in
place, **`cv.lh/api/support/pricing`** (a Worker-only route) routes to the
local Worker container — production-faithful.
