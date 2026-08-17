# How LEco DevOps is wired

Read this when you need to reason about *why* a change lands where it does, or when a
tool result mentions a path or concept you do not recognise.

---

## 1. The edge: Traefik on `*.lh`

Everything a user sees is a hostname under `.lh` resolved to `127.0.0.1` by local DNS
(dnsmasq) and terminated by **Traefik** on ports **80 / 443**, with mkcert certificates
from `certs/`. Traefik is attached to the Docker network **`lh-network`** and reaches
backends **by Docker DNS name on that network only**. It never uses published host ports.

That single fact explains most routing failures. A container can be `healthy` in
`docker ps` and still 502, because Traefik cannot resolve it.

### Where routes actually live

| File | Owner | Meaning |
|------|-------|---------|
| `traefik/dynamic.yml` | the repo (git) | **Canonical** stack routes: dashboard, n8n, Ollama, AirLLM, Cloudflare adapters, file-transfer browser hosts. Edited by contributors. |
| `hosting/traefik/01-stack-core.yml` | generated | A **copy** of `traefik/dynamic.yml` refreshed by `ecosystem-stack/services/traefik.sh ensure-hosting-files` on every Traefik start. |
| `hosting/traefik/dynamic.yml` | generated + merged | The **writable merge target** for hosted apps. `leco-devops ecosystem-register --merge-traefik` and the dashboard Register flow merge app routers/services here. |
| `hosting/traefik/20-dev-stacks.yml` | generated | Routes for every isolated dev stack, regenerated whenever a stack is created/destroyed. |

`hosting/traefik/` is Traefik's **file provider directory**. Traefik watches it and hot
reloads. Two consequences:

- Editing `traefik/dynamic.yml` alone changes nothing at runtime until
  `ensure-hosting-files` (or a Traefik restart, or `leco_platform_traefik_apply`)
  refreshes `01-stack-core.yml`.
- A **syntactically invalid or empty `http:` key** in *any* file in that directory makes
  Traefik drop the whole file provider — which presents as a **global 404 on every
  hostname**, not a single broken route. See `troubleshooting.md` §2.

Router priority for hosted apps is derived from **path-prefix length** (longest prefix
wins), generated in `tools/deploy-cli/leco_app/traefik_fragment.py`. You never hand-write
`priority:` numbers.

---

## 2. Two different app models — do not confuse them

LEco has **two** independent models. They use different directories, different registries,
and different tools.

### Hosted apps (`leco_apps`, `leco_app_*`, `leco_onboard`)

A third-party application repo brought onto the platform.

```
hosting/app-available/<slug>/
  leco.app.yaml                     # bridge  — thin: name, root, localHostProfile
  leco.yaml                         # profile — infrastructure, urls, lifecycle, archetype
  source -> /path/to/real/repo      # symlink to the untouched upstream tree
  docker-compose.leco-hosting.yml   # generated overlay: lh-network + ports:!reset
  docker-compose.leco-runtime.yml   # generated overlay: local edge runtimes (Workers)
  .dev.vars / .dev.vars.example     # operator secrets for Worker runtimes (gitignored)
config/leco-registry.yaml           # id -> manifest path
```

The **effective manifest** is bridge + profile merged. The **resolved root** is
`(manifest_dir / manifest.root)` after following symlinks — all compose and wrangler paths
are relative to it.

Core design rule: **the upstream repo is never modified.** LEco-only concerns go into
overlay compose files under `hosting/app-available/<slug>/`, listed in
`infrastructure.dockerCompose.additionalComposeFilesFromManifest`.

### Dev stacks (`leco_dev_stacks`, `leco_dev_stack_*`)

An isolated Compose project `leco-devstack-<id>` providing databases and toolchains at
specific versions, so two apps can want different MySQL/Node versions on one machine.

```
platform/dev-stacks/<id>/docker-compose.yml, stack.yaml
config/leco-platform.yaml -> dev_stacks[]
hosting/traefik/20-dev-stacks.yml
```

- Internal network `leco-devstack-<id>-internal` carries DB/toolchain traffic.
- Only HTTP services that Traefik must reach also join `lh-network`.
- Databases publish **no host ports** by default — this is deliberate collision avoidance,
  not an oversight. Use `leco_dev_stack_access` / `leco_dev_stack_snapshot` for Docker-DNS
  connection strings.

A hosted app binds to a dev stack via `platform.devStackId` in its `leco.yaml`
(`leco_app_bind_dev_stack`), and **must be redeployed** for the binding to take effect.

---

## 3. The dashboard is the single source of truth

Every MCP tool is an HTTP call to the LEco DevOps dashboard API (`http://localhost:8090`,
also `http://dashboard.lh`). The MCP server does **not** touch Docker, the filesystem, or
compose directly. This means:

- Anything you do through the tools is immediately visible in the dashboard UI, and
  vice-versa. There is no second state to reconcile.
- If the dashboard container is down, **every** tool fails. `leco_server_info` is the only
  one that reports that usefully — call it first when things fail wholesale.
- `leco_control` under the hood runs the same `ecosystem-stack/services/*.sh` scripts the
  CLI does, so behaviour is identical to a human running them.

The dashboard itself runs as `service-dashboard` and is routed at `dashboard.lh`. Restart
it (`leco_control("ai-dashboard", "restart")`) after changes to dashboard Python modules;
a browser hard-refresh is separately needed for `dashboard.js` changes.

### URL probing quirk

Dashboard URL probes for `https://*.lh` are performed **inside Docker** as
`http://traefik<path>` with a `Host: <app>.lh` header — Traefik is only on HTTP :80 inside
the network. If a probe reports `HTTP 0` for a URL that works in a browser, suspect a
stale dashboard container before suspecting the app.

---

## 4. Control targets and dependency order

`leco_control_targets` returns ~37 targets across four groups:

| Group | Prefix | Examples |
|-------|--------|----------|
| `ecosystem` | `stack-` | `stack-ecosystem-all` (bulk) |
| `ecosystem-stack` | `ai-` | `ai-traefik`, `ai-dashboard`, `ai-postgres`, `ai-n8n`, `ai-ollama`, `ai-airllm`, `ai-paperclip`, `ai-paperclip-postgres`, `ai-open-webui`, `ai-update-catalog`, `ai-cloudflare-local`, `ai-infra`, `ai-file-transfer` |
| `infra` | `infra-`, `ft-` | `infra-mysql`, `infra-redis`, `infra-mailpit`, `infra-adminer`, `ft-sftp`, `ft-ftp`, `ft-file-browser`, bulk `stack-infra-all`, `stack-file-transfer-all` |
| `cloudflare-local` | `cf-` | `cf-minio`, `cf-valkey`, `cf-r2-adapter`, `cf-kv-adapter`, `cf-d1-adapter`, `cf-workers-runtime`, `cf-browser-rendering-local`, bulk `stack-cf-all` |

Hosted apps appear as `leco-stack-<slug>` targets.

**Start order that actually matters:**

1. `ai-traefik` — nothing routes until the edge is up.
2. `ai-postgres` **before** `ai-n8n` (n8n crash-loops against a missing DB).
3. `ai-paperclip-postgres` **before** `ai-paperclip`.
4. `cf-minio` / `cf-valkey` before the R2 / KV adapters that back onto them.
5. Hosted apps last — they route through Traefik and often depend on `infra-mysql` or a
   dev stack.

`stack-ecosystem-all` handles the ordering internally; use it for a cold start rather than
sequencing by hand. Use per-target starts when you are recovering a specific failure.

**Start policies** (`leco_control_policies`) decide what a bulk start does per target:
`start`, `stop`, or `offloaded`. A target that "won't start with the stack" is usually
policy-`offloaded`, not broken.

---

## 5. Cloudflare-local

KV / R2 / D1 are **shared adapter services** (`kv.lh`, `r2.lh`, `d1.lh`) backed by Valkey
and MinIO — exactly like production Cloudflare, where bindings are managed APIs, not
containers in your project. An app's `wrangler.toml` bindings are mirrored as *names* on
those adapters by `leco-devops provision-local-cf`, recorded in `leco.local-cf.yaml` beside
the manifest. **`wrangler.toml` is never modified.**

Separately, `infrastructure.runtimes[]` runs the app's **actual Worker** locally in a
`leco-rt-<slug>-<runtime.id>` container (`wrangler dev --local`) so `<slug>.lh/api/*`
matches production route-for-route, including its 404s. That is a different mechanism from
the adapters — see `troubleshooting.md` §7.

Some bindings have no local equivalent at all (`browser`, `vectorize`, `hyperdrive`,
`analytics_engine_datasets`, `send_email`, `mtls_certificates`). Declare them under
`infrastructure.runtimes[].productionOnlyBindings` so they render as *expected* rather than
as failures. Do not chase them.

---

## 6. Naming (non-negotiable)

- Application / UI / CLI product name: **LEco DevOps**
- Project / repository brand: **LEco DevOps Open Project**
- Published CLI entrypoint: **`leco-devops`** (Python import path stays `leco_app`,
  PyPI distribution `leco-app`)

Never write "LEco", "Leco DevOps", or "local-ecosystem" as the product name in
user-visible output or documentation you generate.
