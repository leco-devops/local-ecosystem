# LEco application blueprint

**Audience:** operators, dashboard users, and contributors extending hosting, registration, or `leco-devops`.

This document is the **canonical map** of how a third-party app is represented in local-ecosystem: files on disk, merge rules, hosting materialization, Docker Compose, Cloudflare-local, Traefik, and teardown. For day-to-day commands see **[LECO_USER_MANUAL.md](LECO_USER_MANUAL.md)** and **[DEPLOY_CLI.md](DEPLOY_CLI.md)**. For platform-wide architecture context, see **[ARCHITECTURE.md](ARCHITECTURE.md)**, **[HLD.md](HLD.md)**, and **[LLD.md](LLD.md)**.

---

## 1. Terminology

| Term | Meaning |
|------|---------|
| **Bridge** | `leco.app.yaml` — ties LEco to an app: `name`, `root`, `localHostProfile`, optional `applicationVersion`, optional `configRefs`, optional `localhost.notes`. |
| **Profile** | `leco.yaml` (or path in `localHostProfile`) — `infrastructure`, `urls`, `lifecycle`, `archetype`, `notes`. |
| **Effective manifest** | Bridge + profile `infrastructure` merged (same as `leco-devops` / `load_effective_manifest` in `tools/deploy-cli/leco_app/schema.py`). |
| **Resolved root** | Directory where compose / wrangler paths are relative to: `(manifest_path.parent / manifest.root).resolve()` after following symlinks. |
| **Materialized app** | Read-only app path (`wsp:…`): YAML lives under `hosting/app-available/<slug>/` with a `source` symlink to the real tree. |

---

## 2. Recommended layout (v3)

```
<app-or-hosting-slot>/
  leco.app.yaml          # bridge (lecoAppVersion: "3")
  leco.yaml              # profile: infrastructure.*, urls, lifecycle, …
  leco.local-cf.yaml     # optional; written by provision-local-cf / deploy (not hand-authored)
```

- Put **`infrastructure.dockerCompose`**, **`infrastructure.cloudflare`**, **`infrastructure.routing`** in **`leco.yaml`**, not on the bridge (legacy v2 allowed these on `leco.app.yaml`; v3 keeps the bridge thin).
- **`configRefs`** on the bridge (optional) lists human/tooling paths (wrangler, compose, `.env`, …) relative to resolved root; dashboard **Generate/Save YAML** can refresh **config symlinks** under materialized `app-available/<slug>/`.

---

## 3. Registry and hosting indirection

- **`config/leco-registry.yaml`** stores `id`, `label`, and **`manifest`** path relative to the ecosystem repo root.
- Materialized apps use paths like **`hosting/app-available/<slug>/leco.app.yaml`**.
- **`ecosystem-unregister`** removes the registry row and, when the manifest path is under **`hosting/`**, deletes **`hosting/app-available/<slug>`** (`tools/deploy-cli/leco_app/ecosystem_registry.py`).

---

## 4. `source` symlink (read-only roots)

- Under **`hosting/app-available/<slug>/`**, **`source`** points at the **real app tree** (sibling repo path) so `root: source` on the bridge resolves correctly inside the ecosystem repo.
- If the wizard path ends in a directory named **`source/`** but **`wrangler.toml`** or **`docker-compose.yml`** live in the **parent** repo root, the dashboard promotes the symlink target to that **parent** (`compute_hosting_source_symlink_target` in `dashboard/leco_detect.py`).
- **`root: source`** on the bridge names the **symlink file** under materialization; it is **not** joined as `orig_root/source` on the read-only tree when computing paths for save/register.

See **`hosting/README.md`**.

---

## 5. Docker Compose (optional; configured only in YAML)

The registry and `leco-devops` read **`leco.app.yaml` + `leco.yaml`** (effective manifest). **Docker is not discovered from disk as the source of truth** on **Save YAML** — only **`leco.yaml`’s** optional **`infrastructure.dockerCompose`** tells LEco which compose file(s) to run.

- **`infrastructure.dockerCompose.composeFile`** — primary file path **you choose** (relative to resolved root unless absolute), e.g. **`docker-compose.yml`** or **`../docker-compose.yml`** for a Worker subfolder that shares the repo’s stack.
- **`infrastructure.dockerCompose.additionalComposeFiles`** — optional list of extra **`-f`** files resolved from the **app root** (resolved root): build splits, vendor overrides, etc.
- **`infrastructure.dockerCompose.composeFileFromManifest`** — optional **primary** compose file resolved from the **bridge manifest directory**. When set, it replaces **`composeFile`** as the first **`-f`**: typical pattern is **`include: path: source/docker-compose.yml`** plus hosting-only patches (**`ports: !reset []`**, **`lh-network`**) so upstream repos stay unmodified. See **`hosting/samples/sample-hosting-compose-entry/`**.
- **`infrastructure.dockerCompose.additionalComposeFilesFromManifest`** — optional extra **`-f`** files resolved from the **bridge manifest directory** (parent of `leco.app.yaml`). Use under **`hosting/app-available/<slug>/`** to attach **`lh-network`**, `*.lh` URL env defaults, and similar LEco-only bits **without** editing the upstream application repo. See **`hosting/samples/sample-leco-hosting-overlay/`** and **`hosting/app-available/cvision/docker-compose.leco-hosting.yml`**. Auto-generated entries: **`docker-compose.leco-hosting.yml`** (hosting overlay) and **`docker-compose.leco-runtime.yml`** (local edge-runtime overlay — see §6.1).
- **`infrastructure.runtimes`** — optional list of local edge-runtime declarations. Each entry has **`id`**, **`type`** (e.g. **`cloudflare-workers`**), and adapter-specific fields:
  - **`config`** / **`sourceDir`** / **`port`** / **`image`** — file paths and runtime port.
  - **`devVarsFile`** — secrets file path (auto-detected when literally named **`.dev.vars`** next to **`leco.yaml`**).
  - **`stripBindings`** *(Cloudflare Workers)* — TOML tables to drop from a sanitized in-container **`wrangler.toml`** overlay; default **`["browser"]`**; **`"none"`** disables stripping.
  - **`productionOnlyBindings`** *(Cloudflare Workers, informational)* — bindings the production Worker uses that have no local equivalent (Browser Rendering, Vectorize, Hyperdrive, Analytics Engine, Email Routing producer, mTLS certs). LEco renders these as `expected: production-only` badges so operators don't mistake them for misconfiguration.
  - See **[CF_LECO_SERVICE_MAP.md](../docs/CF_LECO_SERVICE_MAP.md)** for which bindings have local implementations, partial bridges, or are production-only.

  LEco DevOps materializes the list into **`docker-compose.leco-runtime.yml`** beside **`leco.app.yaml`**, exposes each runtime as **`leco-rt-<slug>-<runtime.id>`** on **`lh-network`**, and bind-mounts the per-runtime overlay dir **`hosting/app-available/<slug>/.leco-runtime/<runtime.id>/`** into the container so operators can drop **`d1-bootstrap-<BINDING>.sql`** schemas or a sanitized **`wrangler.toml`** view without touching the upstream repo. On every overlay regeneration the adapter also scans the Worker source for **`env.<UPPER_SNAKE>`** references not declared in wrangler.toml **`[vars]`** or as a binding, and writes a vendor-grouped **`.dev.vars.example`** skeleton into **`hosting/app-available/<slug>/`** (existing files are never overwritten). When a **`.dev.vars`** file is present next to **`leco.yaml`**, the adapter auto-bind-mounts it at **`/app/.dev.vars`** — Wrangler reads it automatically. See §6.1 and **[HOSTED_APPS_TRAEFIK_RUNBOOK.md §7](HOSTED_APPS_TRAEFIK_RUNBOOK.md#7-local-edge-runtimes-workers-pages-vercel-lambda-deno)**.
- **`infrastructure.routing.entries[].upstream`** — optional list of **`{prefix, target, runtime?, service?}`** rules. **`target: runtime`** forwards to a sibling **`runtimes[].id`**; **`target: service`** forwards to a Docker DNS name. Replaces the legacy **`frontend`** / **`apiBackend`** / **`backendHost`** fields when present; Traefik priority is derived from prefix length (longest wins).
- **`leco-devops` / `docker compose`** uses those paths only; it does not invent KV/R2/D1 containers — those bindings are **not** services in your compose file (same idea as production Cloudflare: managed APIs). Local provision targets the ecosystem’s **kv-adapter / r2-adapter / d1-adapter** (see **cloudflare-local** in Docker Desktop), not six extra containers in your app’s compose project.
- **Generate YAML** (first-time materialization) may still **suggest** a compose file when the tree is scanned; **Save YAML** does **not** inject or overwrite **`dockerCompose`** unless you already set paths (see `allow_compose_discovery` in `dashboard/leco_detect.py`).
- If the primary compose file is **missing**, **`leco-devops down`** exits **0** with a warning; offboard still runs.

---

## 6. Cloudflare / Wrangler vs Docker

| Artifact | Role |
|----------|------|
| **`infrastructure.cloudflare.wranglerConfig`** | Path to `wrangler.toml`; **provision** and **deploy** hooks read this file. |
| **`infrastructure.wranglerBindingPreview`** | Informational mirror (KV/R2/D1 rows) for UI; **does not** create Docker services. |
| **`leco.local-cf.yaml`** | Records **names** of namespaces / buckets / databases provisioned on the **shared** local adapters; still not per-binding containers in your repo. |
| **Local CF provision** | Creates those resources on **kv.lh / r2.lh / d1.lh** adapters; look under the **cloudflare-local** stack in Docker, not inside your app’s compose project. |

### 6.1 Local edge runtimes (Workers, Pages, Vercel, Lambda, Deno)

When production routes traffic through an edge runtime, the local compose
backend usually implements only a subset of those routes and the rest
404. LEco DevOps closes the gap by running the runtime locally — without
modifying the upstream repo.

| Concept | Where |
|---------|-------|
| **Schema** (`LocalRuntimeSpec`, `RoutingUpstreamRule`) | `tools/deploy-cli/leco_app/schema.py` |
| **Adapter registry** (one module per `type`) | `dashboard/leco_runtimes/` — `cloudflare_workers.py` is implemented; `cloudflare_pages.py`, `vercel.py`, `aws_lambda.py`, `deno_deploy.py` are roadmap stubs. |
| **Reference container images** | `infra/runtimes/<type>/` — currently `cloudflare-workers/`. |
| **Overlay materialization** | `dashboard/leco_detect.py::ensure_local_runtime_overlay` writes `docker-compose.leco-runtime.yml` beside the manifest and patches `additionalComposeFilesFromManifest`. |
| **Traefik routers** | `tools/deploy-cli/leco_app/traefik_fragment.py::_upstream_routing_fragment` emits one router per `routing.entries[].upstream[]` rule. |
| **Onboarding hint** (Worker paths + suggested YAML) | `dashboard/leco_runtimes/cloudflare_workers.py::_scan_worker_paths` + `suggested_upstream_yaml`, surfaced through `leco-devops runtimes --detect` and the registration wizard log. |
| **Secret scanner + `.dev.vars.example` generator** | `dashboard/leco_runtimes/cloudflare_workers.py::detect_expected_secrets` + `render_dev_vars_example`. Walks Worker source for `env.<NAME>` refs, subtracts wrangler.toml `[vars]` keys + bindings + a small SDK ignore-list, and writes a vendor-grouped skeleton (LLM / Payments / Email / Cloudflare / Other) into `hosting/app-available/<slug>/.dev.vars.example`. Existing files are never overwritten. |
| **Auto-bind `.dev.vars`** | `dashboard/leco_runtimes/cloudflare_workers.py::compose_service` — if `<manifest_dir>/.dev.vars` exists it is bind-mounted at `/app/.dev.vars` even without an explicit `devVarsFile`. |
| **D1 bootstrap + resilient migrations** | `infra/runtimes/cloudflare-workers/entrypoint.sh` — applies `d1-bootstrap-<BINDING>.sql` once (sentinel-tracked), then loops `wrangler d1 migrations apply` past per-file failures. |
| **Wrangler binding sanitization** | `dashboard/leco_runtimes/cloudflare_workers.py::_sanitize_wrangler_toml` strips listed top-level TOML sections (default `[browser]`); the sanitized copy is bind-overlaid at `/app/wrangler.toml` inside the container. |
| **CLI diagnostic** | `leco-devops runtimes -f leco.app.yaml` (defaults to `--detect`). |
| **Sample manifest** | `hosting/samples/sample-cf-worker-runtime/`. |

Zero-touch upstream contract:

1. **Source tree**: bind-mounted at `/app` with `/app/node_modules` and
   `/app/.wrangler` masked by LEco-owned named volumes — installs and
   Miniflare state never write back to the upstream tree.
2. **wrangler.toml**: a sanitized copy (per `stripBindings`) is bind-mounted
   *on top of* `/app/wrangler.toml` inside the container, leaving the
   upstream file untouched on disk.
3. **D1 schema**: operator-owned `d1-bootstrap*.sql` under
   `hosting/app-available/<slug>/.leco-runtime/<runtime_id>/` is applied
   once on first boot via `wrangler d1 execute --file`, then `wrangler d1
   migrations apply` runs in a bounded retry loop that records each
   failing migration in `d1_migrations` so the batch can progress past it.
4. **Secrets**: live under `hosting/app-available/<slug>/.dev.vars`
   (gitignored), never in the upstream repo.

---

## 7. Traefik

- **`ecosystem-register --merge-traefik`** merges **routing-derived** keys from the **effective** manifest into **`hosting/traefik/dynamic.yml`** (stack routes remain in **`traefik/dynamic.yml`** and are copied to **`hosting/traefik/01-stack-core.yml`** on Traefik start).
- In v3, **`infrastructure.routing`** normally lives in **`leco.yaml`**. If neither **`routing.entries`** nor **`cloudflare.localCfPublicPrefix`** is set, register logs that Traefik merge was skipped.
- The **Hosted apps** UI reads routing from the bridge file **and** from **`infrastructure.routing`** in the profile file (`dashboard/hosted_apps.py`).
- **Operations / incidents:** **[HOSTED_APPS_TRAEFIK_RUNBOOK.md](HOSTED_APPS_TRAEFIK_RUNBOOK.md)** — 502, **`lh-network`**, Docker DNS vs **`routing.entries`**, dashboard URL probes, same-origin **`/api`** on `*.lh`.

---

## 8. Dashboard flows (registration)

| Step | API / behavior |
|------|----------------|
| Detect | `POST /api/leco/detect` — scan path for compose, wrangler, archetype. |
| Generate YAML | `POST /api/leco/generate-yaml` — write bridge + profile; materialized roots refresh **`source`** + **config symlinks**. |
| Save YAML | `POST /api/leco/save-yaml` — validate Pydantic schemas, same symlink rules; **Save** runs path normalization against resolved tree **after** refreshing **`source`**. |
| Register | `POST /api/leco/register` — requires YAML on disk; **`leco-devops ecosystem-register`**; optional **`deploy`**. |

**Hosted apps list:** entries come from the registry (manifest paths only). Per-app **compose controls** need an **effective** **`dockerCompose`** block when you use Docker; **Workers-only** apps omit it and use the worker-only control path in `dashboard/leco_control.py`. **Remove / Reset** on `leco-stack-<id>`: **`leco-devops ecosystem-unregister`** runs **local CF teardown** (when enabled) before **`docker compose down`** so dedicated adapters stay reachable, then Traefik / registry (`dashboard/control.py`, `dashboard/hosted_offboard.py`).

---

## 9. Code map (maintainers)

| Concern | Location |
|---------|----------|
| Bridge/profile schema, merge | `tools/deploy-cli/leco_app/schema.py` |
| Compose CLI args | `tools/deploy-cli/leco_app/compose_runner.py` |
| Register/unregister, hosting dir removal | `tools/deploy-cli/leco_app/ecosystem_registry.py` |
| Dashboard scan, defaults, YAML materialize | `dashboard/leco_detect.py`, `dashboard/leco_materialize.py` |
| Register/deploy API | `dashboard/leco_registration.py`, `dashboard/app.py` |
| Hosting symlink helpers | `dashboard/hosting_layout.py` |
| Control remove/reset + offboard | `dashboard/control.py` |
| Offboard wrapper | `dashboard/hosted_offboard.py` |
| Hosted apps API | `dashboard/hosted_apps.py`, `dashboard/leco_control.py` |

---

## 10. Future work / extension ideas

- **Port detection** for `deploy` could scan **additional** compose files (today `detect_compose` focuses on the app root walk).
- **`ecosystem-unregister`** currently may abort registry removal if **local CF teardown** fails; operators can use **`--no-clean-local-cf`**; a **`--force`** that still unregisters is a possible enhancement.
- **Traefik** keys from profile-only manifests could be unified further in `traefik_manifest_keys` if new shapes appear.
- **Zip-uploaded** apps without a `source` symlink use the extracted tree as root; reference YAML packs live in **`hosting/samples/`** (not under **`hosting/app-available/`**).

---

## See also

- **[HOSTED_APPS_TRAEFIK_RUNBOOK.md](HOSTED_APPS_TRAEFIK_RUNBOOK.md)** — Hosted apps behind Traefik: symptoms, fixes, code map.
- **[hosting/README.md](../hosting/README.md)** — directory layout and zip upload.
- **[hosting/app-available/README.md](../hosting/app-available/README.md)** — materialization layout; **[hosting/samples/README.md](../hosting/samples/README.md)** — reference manifest packs.
- **[tools/deploy-cli/README.md](../tools/deploy-cli/README.md)** — package overview and local CF policy.
