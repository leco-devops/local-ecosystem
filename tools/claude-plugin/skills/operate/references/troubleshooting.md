# Failure modes that actually happen

Ordered roughly by how often they occur. Each entry gives the **symptom you will observe
through the tools**, the real cause, and the minimal fix. Diagnose before acting; almost
none of these are fixed by redeploying.

---

## 1. 502 Bad Gateway on `<slug>.lh` — containers are not on `lh-network`

**Symptom.** `leco_apps` shows the app running. `leco_app_snapshot(slug,
sections=["runtime","urls"])` shows compose services `running`/`healthy`, but the main URL
probe returns **502**. `leco_traefik_routes(hostname="<slug>.lh")` shows the router and
service exist.

**Cause.** Traefik is attached to `lh-network` only and resolves backends by Docker DNS on
that network. The app's compose project created its own default network, so the backend
name does not resolve for Traefik. Container health is irrelevant here.

**Fix.** The hosting overlay `hosting/app-available/<slug>/docker-compose.leco-hosting.yml`
must join the frontend/API services to `lh-network`, and must be listed under
`infrastructure.dockerCompose.additionalComposeFilesFromManifest`. `leco_app_validate(slug)`
reports when it is missing; re-running `leco_register(path, app_id)` (or the dashboard
Register/Validate flow) regenerates it via `ensure_lh_network_hosting_overlay()`. Then
`leco_app_control(slug, "recreate")` so compose picks up the extra `-f`.

**Do not** "fix" this by publishing host ports. Traefik does not use them.

---

## 2. 502 — Traefik's upstream name does not match Docker DNS

**Symptom.** Same as §1, but the overlay is present and the containers *are* on
`lh-network`. `leco_traefik_routes` shows a service URL like `http://cvision-frontend-1:3000`
while `leco_app_snapshot(...,["runtime"])` lists a container named `cv-frontend`.

**Cause.** Traefik's `loadBalancer` URL is derived from the compose service, but the compose
file sets an explicit `container_name`, which wins over the Compose-generated
`{project}-{service}-1` form. Or the registry id was renamed and `routing.entries` still
carry the old prefix.

**Fix.** Compare intent against reality:

1. `leco_route_fragment_from_app(slug)` — the routes the manifest *implies*.
2. `leco_traefik_routes(hostname="<slug>.lh")` — the routes Traefik *has*.
3. Diff them. If the manifest is right and Traefik is stale, merge with
   `leco_route_merge_fragment(yaml_fragment)`.
4. If the manifest itself is wrong, `leco_app_validate(slug)` normally remaps it —
   the dashboard's `normalize_profile_compose_backend_hosts()` /
   `_remap_stale_compose_dns_host()` run on Validate and Register.

Rule of thumb: **use `container_name` when the upstream compose sets one; otherwise
`{projectName}-{service}-1`.**

---

## 3. Global 404 on *every* hostname — stale or invalid `hosting/traefik/01-stack-core.yml`

**Symptom.** Not one app but **everything** 404s, including `dashboard.lh` and `traefik.lh`.
`leco_urls(only_unhealthy=True)` returns essentially the whole list. Traefik itself is
running. Or: a newly added platform host (e.g. `paperclip.lh`) 404s while everything else
works.

**Cause.** `hosting/traefik/` is Traefik's file-provider directory.
`hosting/traefik/01-stack-core.yml` is a **generated copy** of `traefik/dynamic.yml`,
refreshed on Traefik start. If it went stale (new host added to the canonical file but never
copied) that host is missing. If any file in the directory has an **empty or invalid `http:`
key**, Traefik drops the provider and every route disappears at once.

**Fix.** `leco_platform_traefik_apply()` — regenerates platform routes and runs the
Traefik heal path. Without MCP: `./ecosystem-stack/ecosystem-stack.sh heal traefik`, or
`./leco-cli.sh traefik heal`, or `./leco-cli.sh traefik ensure-files`.

Distinguish this from a single-app 404, which is §4.

---

## 4. One app 404s — Traefik merge never happened

**Symptom.** `leco_apps` lists the app (possibly `pending_registration=true`), containers
are up, but `<slug>.lh` returns 404 rather than 502. `leco_traefik_routes(hostname=...)`
returns nothing.

**Cause.** Deploying an app does **not** route it. Routing happens only when the app's
routing-derived keys are merged into `hosting/traefik/dynamic.yml` — which is what
`leco-devops ecosystem-register --merge-traefik` does. An app deployed with `leco_control`
or `docker compose` alone is invisible to Traefik.

Also check that the manifest has routing at all: if neither
`infrastructure.routing.entries` nor `cloudflare.localCfPublicPrefix` is set, register logs
that the Traefik merge was **skipped** and exits successfully. A "successful" register with
no routes is the single most common silent failure in onboarding.

**Fix.** `leco_register(path, app_id, deploy=False)` to re-merge, or
`leco_route_fragment_from_app(slug)` → `leco_route_merge_fragment(fragment)`. Traefik
reloads the file provider automatically; no restart needed.

**404 = no route. 502 = route exists, backend unreachable.** Always establish which one you
have before touching anything.

---

## 5. `port is already allocated` on deploy

**Symptom.** `leco_app_control(slug, "deploy")` or `leco_onboard` fails with
`Bind for 0.0.0.0:80 ... port is already allocated` (or `:3000`, `:5432`, `:8001`).

**Cause.** The upstream compose publishes host ports that LEco core or another stack already
owns. Port 80/443 belong to Traefik permanently.

**Fix.** Never edit the upstream repo. The hosting overlay must carry `ports: !reset []` for
every service that publishes ports upstream. Onboarding generates this automatically for new
overlays; older overlays may predate it. Re-run `leco_app_validate(slug)` then
`leco_register(...)` to regenerate, or read/patch the overlay yourself.

Traefik reaches the container **on the container port over `lh-network`**, so removing the
host publish costs you nothing.

---

## 6. SPA on `https://<slug>.lh` calls `http://localhost:8001/api`

**Symptom.** Page loads, API calls fail in the browser with CORS or connection-refused.
Backend logs (`leco_app_logs`) show no requests at all.

**Cause.** Upstream compose bakes `REACT_APP_BACKEND_URL=http://localhost:8001` (or the
framework equivalent) into the frontend build, which beats any same-origin logic.

**Fix.** The hosting overlay sets `REACT_APP_BACKEND_URL: ""` and
`REACT_APP_SITE_URL: https://<slug>.lh`. That is generated by
`_lh_overlay_env_for_service()`; re-register to refresh it. The app must then be **rebuilt**,
not just restarted — `leco_app_control(slug, "recreate")`.

---

## 7. Worker-only API routes 404 (or return the SPA shell) on `<slug>.lh`

**Symptom.** `<slug>.lh/api/...` returns 404 in LEco but works in production. Or a path like
`/health/json` returns the frontend HTML (`You need to enable JavaScript to run this app.`)
instead of JSON.

**Cause.** In production those paths are served by a Cloudflare Worker. Locally, routing
points `/api` at a compose backend that implements only a subset — or has no rule for that
path at all, so Traefik falls through to the catch-all `/` router at the frontend.

**Fix.** Two parts.

1. Declare the runtime in `leco.yaml`:
   `infrastructure.runtimes[] = [{id: worker, type: cloudflare-workers, config: wrangler.toml, port: 8787}]`.
   LEco materializes `docker-compose.leco-runtime.yml` and runs `wrangler dev --local` in a
   `leco-rt-<slug>-worker` container on `lh-network`.
2. Point routing at it:
   `routing.entries[].upstream = [{prefix: /api, target: runtime, runtime: worker}, {prefix: /, target: service, service: {host: <slug>-frontend, port: 3000}}]`.
   Router priority is derived from prefix length, so a longer prefix such as `/health/json`
   automatically outranks `/api` which outranks `/`. Do not hand-set priorities.

Re-merge Traefik after editing (`leco_register` or `leco_route_merge_fragment`).

### Runtime container in `Restarting`

- Logs say *"Browser Rendering is not supported locally"* → upstream `wrangler.toml` declares
  a binding Miniflare cannot simulate. The adapter strips `[browser]` by default into a
  sanitized in-container copy; add more via `infrastructure.runtimes[].stripBindings`.
- Logs say `spawn …/workerd ENOENT` → the runtime image was built on musl. It must be
  glibc (`node:22-bookworm-slim`). Rebuild from `infra/runtimes/cloudflare-workers/` **and**
  delete the per-app node_modules volume (`docker volume rm <project>_leco-rt-<slug>-<runtime>-node-modules`)
  before redeploying — musl-built artifacts must be evicted.

### `D1_ERROR: no such table`

The local D1 file is empty. Drop the baseline schema into
`hosting/app-available/<slug>/.leco-runtime/<runtime_id>/d1-bootstrap-<BINDING>.sql` and
redeploy; the entrypoint applies it once (sentinel-tracked) before running migrations.

### Worker health board shows many services `down`

Two distinct causes, and they need opposite responses:

- **Missing operator secrets.** LEco auto-writes
  `hosting/app-available/<slug>/.dev.vars.example` listing every `env.<UPPER_SNAKE>` the
  Worker references that is not in `wrangler.toml`. Copy to `.dev.vars`, fill in, redeploy.
  The adapter bind-mounts it automatically.
- **Production-only bindings.** `browser`, `vectorize`, `hyperdrive`,
  `analytics_engine_datasets`, `send_email`, `mtls_certificates` have **no local
  equivalent** and will always read `down`. Declare them under
  `infrastructure.runtimes[].productionOnlyBindings` so they render as expected. **Do not
  attempt to fix these.** Tell the user they are expected.

---

## 8. 503 "Backend fetch failed" from a Varnish error page

This is **not** a Traefik problem — Traefik reached a backend, and that backend (Varnish)
could not reach *its* backend. Usually Varnish started before the Node/Express server was
listening, or the server container is crash-looping.

Fix: healthcheck on the app server plus `depends_on: {server: {condition: service_healthy}}`
on varnish, `LECO_DISABLE_VARNISH_NCSA=true`, and a VCL backend of `<slug>-server:3000`.
The `sample-node-varnish-multiprocess` pack under `hosting/samples/` has the working shape.

---

## 9. Dashboard reports `HTTP 0` for a URL that works in the browser

The dashboard probes `https://*.lh` from **inside Docker** as `http://traefik<path>` with a
`Host:` header, because Traefik only listens on HTTP :80 inside the network. A dashboard
container running older probe logic reports `HTTP 0`.

Fix: `leco_control("ai-dashboard", "restart")`. Also, the hosted-app *list* endpoint caches
`main_url_probe` (90 s + localStorage), so a list view can disagree with a fresh
`leco_app_snapshot`. **Trust the snapshot's `url_probes`, not the list.**

---

## 10. n8n / Paperclip crash-looping right after a bulk start

Dependency order. `ai-postgres` must be healthy before `ai-n8n`; `ai-paperclip-postgres`
before `ai-paperclip`. On a cold start prefer `leco_control("stack-ecosystem-all", "start")`,
which sequences internally, over starting targets individually.

If Paperclip logs `EACCES` on `.env`, root-owned files under `/paperclip/instances` are the
cause; start/bootstrap heal ownership, so a plain restart usually clears it.

---

## 11. Model actions return `unauthorized`

The dashboard was started with `DASHBOARD_CONTROL_TOKEN` set, and the MCP server has no
matching token. Read-only tools keep working; anything mutating returns 401.

`leco_server_info` reports `control_token_configured`. `leco-mcp doctor` additionally reports
`control_token_required_by_dashboard` and warns on the mismatch. Fix by setting
`LECO_MCP_CONTROL_TOKEN` to the dashboard's `DASHBOARD_CONTROL_TOKEN` and restarting the MCP
server. **You cannot fix this from inside a tool call** — report it to the user.

---

## 12. Dev stack problems

| Symptom | Action |
|---------|--------|
| Stack routes wrong / container fell off `lh-network` / image name deprecated | `leco_dev_stack_action(id, "repair")` — **keeps volumes and manual edits** |
| Template config is wrong (bad MariaDB version, broken CMS install) | `leco_dev_stack_action(id, "reinstall", confirm=True)` — regenerates from template, `down -v`, **wipes data**, reverts manual Advanced edits |
| Stack no longer wanted | `leco_dev_stack_action(id, "destroy", confirm=True)` — removes containers, volumes, `platform/dev-stacks/<id>/`, the registry row, and regenerates `hosting/traefik/20-dev-stacks.yml` |
| Framework stack (Laravel, Django, NestJS…) seems hung on first start | It is installing dependencies inside the app container. Watch `leco_dev_stack_snapshot` or compose logs; do not reinstall. |

`repair` is almost always the right first move. `reinstall` and `destroy` both require
`confirm=True` **and** a server started with `LECO_MCP_ALLOW_DESTRUCTIVE=1`.

An app bound to a dev stack via `leco_app_bind_dev_stack` does not pick up the binding until
it is **redeployed**.

---

## 13. Everything fails at once

Call `leco_server_info` first. Either:

- `reachable: false` → the dashboard is down. Nothing else will work. Start it outside MCP:
  `./leco-cli.sh dashboard start` (or `./ecosystem-stack/ecosystem-stack.sh start dashboard`).
- `reachable: true` but Traefik is down → §3, plus `leco_control("ai-traefik", "start")`.
