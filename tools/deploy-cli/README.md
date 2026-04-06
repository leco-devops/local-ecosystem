# LEco DevOps — multi-app deploy CLI

This CLI is part of the **LEco DevOps Open Project** and is released under the **MIT License** (see [../../LICENSE](../../LICENSE)).

**LEco DevOps** is the product name for this tooling. The command-line programs are **`leco-app`** and **`leco-devops`** (same tool). They inspect an application repository, write a small manifest (`leco.app.yaml`), and run **Docker Compose** and optionally **Wrangler** lifecycle commands. The CLI is **orthogonal** to the local-ecosystem core: third-party apps stay in their own repos and compose files.

## Resource model (one package per app)

- **One manifest per application** lists what that app uses: compose file, optional Wrangler config, optional Traefik routing hints, health URLs.
- By default the CLI **does not** start MinIO/Valkey for you — that is **local-ecosystem’s** shared **`cloudflare-local`** stack. When policy allows (see below), **`leco-app deploy`**, **`leco-app init`** (prompt or `--provision-local-cf`), **`leco-app ecosystem-register`**, and **`leco-app onboard`** **create or refresh** KV/R2/D1 **names** on those shared adapters from your **wrangler.toml** bindings. Your **`wrangler.toml` is never modified.** Output: **`leco.local-cf.yaml`** next to the manifest (adapter bases + local names). Override bases with **`LECO_LOCAL_KV_URL`**, **`LECO_LOCAL_R2_URL`**, **`LECO_LOCAL_D1_URL`** (written to **`leco.local-cf.yaml`**). For HTTP calls from **service-dashboard**, set **`LECO_LOCAL_KV_INTERNAL_URL`** (e.g. `http://kv-adapter:8082`) so provision hits adapters on **`lh-network`**; **`dashboard.sh`** sets these by default. Use **`LECO_LOCAL_CF_INSECURE_SSL=1`** only if you must skip TLS verify.
- **Per-app adapters (Docker Desktop under your project):** set **`cloudflare.dedicatedLocalAdapters: true`** in **`leco.yaml`** / profile (merged with **`leco.app.yaml`**). Merge **`docker-compose.leco-dedicated-cf.example.yml`** from **`hosting/samples/sample-cloudflare-application/`** via **`additionalComposeFiles`**, set **`LECO_CF_ROOT`** in **`.env`** to the absolute path of this repo’s **`cloudflare-local`** directory, and **`docker compose up`** so **`leco-local-{kv,r2,d1}-adapter`** (and Valkey/MinIO) run **inside your compose project** and join **`lh-network`**. Provision/teardown then use **`http://leco-local-kv-adapter:8082`** (R2 **8081**, D1 **8083**) unless you override **`LECO_DEDICATED_*_ADAPTER_URL`**. **`leco.local-cf.yaml`** stores the same bases for your app containers.
- **Apps without** `cloudflare.wranglerConfig` never hit these adapters (compose-only or non-Workers stacks).
- **`leco-app provision-local-cf`** always attempts provisioning when a wrangler path exists (ignores manifest **`provisionLocalResources`** and **`LECO_PROVISION_LOCAL_CF`** so you can repair a stack manually).

### Local CF provision policy (generic defaults)

Precedence toward **skipping** local KV/R2/D1 creation:

1. **`--no-provision-local-cf`** on **`deploy`** / **`ecosystem-register`** / **`onboard`** / **`init`** paths that support it.
2. Environment **`LECO_PROVISION_LOCAL_CF`** set to **`0`**, **`false`**, **`no`**, or **`off`**.
3. No **`cloudflare.wranglerConfig`** in the manifest.
4. Manifest **`cloudflare.provisionLocalResources: false`**.

Otherwise, when a wrangler config path is set, hooks run by default. Internal plan types live in **`leco_app.resource_plan`**; **wrangler.toml** is mapped in **`leco_app.wrangler_cf_resources`** only—add new binding kinds or backends there and in **`local_cf_provision`** without app-specific logic.

**Not provisioned locally** (use real Cloudflare or other local-ecosystem services): Wrangler **browser**, **queues**, **Durable Objects**, **Hyperdrive**, **Vectorize**, **assets** hosting, etc.—only **KV / R2 / D1** tables in wrangler are mirrored to kv.lh / r2.lh / d1.lh today.

- **Multiple Workers** locally = multiple **projects** (separate manifests / compose files), not one command spawning many Workers unless your compose defines that.

## Install

Install from **this directory** (`deploy-cli/`), not from the parent `tools/` folder — only here is `pyproject.toml` present.

From the **local-ecosystem repository root**:

```bash
cd tools/deploy-cli
pip install -e .
leco-app --help    # or: leco-devops --help
```

Python **3.11+** required.

## Quick start

**New app in local-ecosystem (deploy + Hosted apps + Traefik):**

```bash
cd /path/to/your/app   # directory containing leco.app.yaml (or use -f)
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
leco-app onboard         # compose up, leco-registry.yaml, merge routing.entries → traefik/dynamic.yml
# or: leco-app onboard -f ./cloudflare/leco.app.yaml -E /path/to/local-ecosystem
```

**Iterating on manifests only:**

```bash
cd /path/to/your/app
leco-app init              # existing leco.app.yaml + leco.yaml → validate & deploy; else wizard, then deploy
# or
leco-app init -y           # defaults only (compose + wrangler detection)
# minimal manifest without compose (TTY confirm):
leco-app init --manifest-only

leco-app detect            # JSON scan: compose, wrangler, archetype (for LEco DevOps / scripts)
leco-app run-hooks --phase prepare   # run merged profile lifecycle.prepare commands

# During init, if you add Traefik routes: press Enter on an empty hostname to stop adding routes.
# Choose "split route" for React + API: generates frontend + apiBackend in leco.app.yaml and
# traefik-fragment output with Host+PathPrefix(/api) → backend, Host → UI. Put those containers on lh-network.

leco-app init --onboard -E /path/to/local-ecosystem   # after deploy: register + Traefik merge (same as parts of onboard)
leco-app deploy            # docker compose up -d --build; then local KV/R2/D1 from wrangler if policy allows
leco-app deploy --no-provision-local-cf   # compose only
leco-app status
leco-app logs -f
leco-app down              # compose down only

# Staging / offload: strip Traefik routes + compose down -v (auto-detects traefik/dynamic.yml from -E)
leco-app offload --cwd hosting/app-available/myapp -E /path/to/local-ecosystem
# Or with explicit Traefik path:
leco-app offload --traefik-dynamic /path/to/local-ecosystem/hosting/traefik/dynamic.yml

# Scaffold a new app from the multi-process Node+Varnish template:
leco-app scaffold myapp -E /path/to/local-ecosystem --source-path /abs/path/to/source

leco-app traefik-fragment  # print YAML to paste into traefik/dynamic.yml
leco-app traefik-fragment -o /tmp/traefik-snippet.yml

leco-app cf-secrets-checklist --env staging
leco-app cf-deploy --env staging
leco-app cf-deploy --env production --confirm-production
```

State/metadata: `~/.local/share/leco/apps/<slug>/` (override with `XDG_DATA_HOME`).

## Manifest (`leco.app.yaml`)

Written next to the app root (or path given to `--out`). Uses **camelCase** keys for compatibility with common tooling conventions:

| Field | Purpose |
|-------|---------|
| `name` | Slug; used for state directory |
| `root` | `.` = manifest directory |
| `dockerCompose.composeFile` | Path to compose file relative to `root` |
| `dockerCompose.additionalComposeFiles` | Optional extra `-f` files (merged after the primary file; paths relative to `root`) |
| `dockerCompose.envFile` | Optional `--env-file` |
| `dockerCompose.projectName` | Optional `docker compose -p` |
| `dockerCompose.profiles` | Compose profiles |
| `cloudflare.wranglerConfig` | Path to `wrangler.toml` |
| `cloudflare.wranglerEnv` | Default `--env` for Wrangler |
| `cloudflare.provisionLocalResources` | Default `true`; set `false` to skip local KV/R2/D1 on deploy/register (unless you run **`provision-local-cf`**) |
| `cloudflare.localCfPublicPrefix` | Optional short label (e.g. `cv`): provision + `leco.local-cf.yaml` use `https://{prefix}-kv.lh`, `-r2.lh`, `-d1.lh`; **`ecosystem-register --merge-traefik`** adds Host routes to the shared adapters. Browser bindings stay shared. |
| `routing.entries` | Traefik fragment: legacy backend **or** split `frontend` + `apiBackend` |
| `traefikCleanup` | Optional explicit router/service keys for `leco-app offload` when names differ from fragment defaults |
| `healthcheckUrls` | URLs probed by `leco-app status` |
| `lecoAppVersion` | Use `"2"` when using `localHostProfile` / `localhost` |
| `localHostProfile` | Optional path to sidecar profile (default filename `leco.yaml`; relative to manifest dir). `localhost.yaml` / `leco.localhost.yaml` still work |
| `localhost` | Optional inline same schema as the sidecar file (merged over file) |

**Sidecar profile** (`leco.yaml`, v1): `schemaVersion`, optional `archetype`, `urls[]` (logical endpoints by `role`), `lifecycle` (`prepare` / `build` / `preStart` command steps), `notes`. Does not auto-generate Traefik routes; keep using `routing.entries` for Traefik fragments.

## Commands

| Command | Description |
|---------|-------------|
| `onboard` | **Deploy + `ecosystem-register` + merge `routing.entries` into `traefik/dynamic.yml`** (needs `-E` or `LECO_ECOSYSTEM_ROOT`) |
| `init` | Detect compose + wrangler + `conf/` + `leco-docker-preload.js` + hosting overlay; prompts; write manifest + `leco.yaml` stub; `--onboard -E …` adds register + Traefik merge after deploy |
| `detect` | Print JSON detection result |
| `run-hooks` | Run `prepare`, `build`, or `preStart` from merged localhost profile |
| `deploy` | `docker compose up -d --build`; then local KV/R2/D1 from wrangler when **`cloudflare.wranglerConfig`** is set and policy allows (`--no-provision-local-cf` to skip) |
| `stop` | `docker compose stop` |
| `down` | `docker compose down` (`-v` optional) |
| `offload` | **Staging**: strip Traefik routes (auto-detected from `-E` / `LECO_ECOSYSTEM_ROOT`) + `compose down -v --remove-orphans` (keeps `app-available/` files). Use `--no-volumes` to keep data. Mirrors dashboard **staging** button |
| `scaffold` | Generate `hosting/app-available/<slug>/` from a sample template with placeholder replacement (`--template`, `--source-path`, `--dry-run`) |
| `ecosystem-register` | Add app to `local-ecosystem/config/leco-registry.yaml`; also provisions local KV/R2/D1 from Wrangler unless `--no-provision-local-cf`; **`--merge-traefik`** updates `traefik/dynamic.yml` |
| `provision-local-cf` | Re-run KV/R2/D1 creation from manifest’s Wrangler config |
| `ecosystem-unregister` | Remove registry id; **local CF teardown before `compose down`** (dedicated adapters); then Traefik strip from `traefik/dynamic.yml` (`--no-strip-traefik` / `--no-clean-local-cf` / `--no-compose-down` to skip parts) |
| `logs` | `docker compose logs` (`-f`, `--tail`, `--service`) |
| `status` | `docker compose ps` + optional HTTP checks |
| `traefik-fragment` | Emit YAML for manual merge into `traefik/dynamic.yml` |
| `cf-deploy` | `wrangler deploy -c …` (requires `wrangler login`) |
| `cf-secrets-checklist` | Heuristic list of `[vars]` keys that may need `wrangler secret put` |

## Traefik

Use **`onboard`** or **`init --onboard`** or **`ecosystem-register --merge-traefik`** to merge `routing.entries` into `traefik/dynamic.yml` (atomic write + `.bak`). For a printable snippet only, use **`traefik-fragment`** and merge manually.

## See also

- [docs/LECO_APP_BLUEPRINT.md](../../docs/LECO_APP_BLUEPRINT.md) — v3 bridge vs profile, hosting materialization, **`additionalComposeFiles`**, offboard semantics, code map
- [docs/LECO_USER_MANUAL.md](../../docs/LECO_USER_MANUAL.md) — user manual (workflows, LEco DevOps UI, troubleshooting); listed in the LEco DevOps **Docs** tab
- [docs/DEPLOY_CLI.md](../../docs/DEPLOY_CLI.md) in the repo root
- [docs/DEPLOY_CUSTOM_APPS.md](../../docs/DEPLOY_CUSTOM_APPS.md)
