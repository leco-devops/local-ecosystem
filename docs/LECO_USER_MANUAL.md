# LEco DevOps — User manual

**LEco DevOps** is the product name for the multi-app deploy tooling in `tools/deploy-cli/`. You run it as **`leco-app`** or **`leco-devops`** (identical CLIs).

This guide explains **what LEco DevOps is**, **how to use it day to day**, and **how the CLI connects to the LEco DevOps web UI**. For exhaustive command-line and YAML tables, see **[DEPLOY_CLI.md](DEPLOY_CLI.md)** (also listed in the **Docs** tab). For architecture context, see **[ARCHITECTURE.md](ARCHITECTURE.md)**, **[HLD.md](HLD.md)**, **[LLD.md](LLD.md)**, and **[LECO_TOOLING.md](LECO_TOOLING.md)**.

---

## What is LEco DevOps?

**LEco DevOps** is a small CLI for **third-party applications** you keep **outside** the local-ecosystem `ecosystem-stack`. Each app gets:

- A **bridge** manifest: **`leco.app.yaml`** — **`name`**, **`root`**, **`localHostProfile`**, optional **`configRefs`**, optional **`applicationVersion`**, optional **`localhost.notes`**. With **`lecoAppVersion: "3"`**, put **Docker Compose, Cloudflare, and Traefik routing** in **`leco.yaml`** under **`infrastructure`** (effective manifest = merge of bridge + profile).
- A **profile**: **`leco.yaml`** (or **`localhost.yaml`** / inline `localhost:`) — archetype, **`urls`**, **`lifecycle`**, and (v3) **`infrastructure`**.

The ecosystem **`config/leco-registry.yaml`** lists registered apps so the **Hosted apps** tab in LEco DevOps can monitor compose stacks, logs, and manifest excerpts.

---

## When to use LEco DevOps vs something else

| Goal | Use |
|------|-----|
| First-party ecosystem stack (Ollama, WebUI, Traefik, LEco DevOps **Control** targets, …) | `ecosystem-stack` scripts and **Control** tab — not **`leco-app`** for those stacks |
| A separate repo or folder with **docker compose** | **`leco-app init`** → **`deploy`** → **`ecosystem-register`** |
| **Cloudflare Workers** with **Wrangler** + optional compose | LEco DevOps: manifest **`cloudflare.wranglerConfig`**; **`cf-deploy`**, **`provision-local-cf`** as needed |
| **WordPress, Magento, Node, PHP**, etc. without Workers | **`leco.yaml`** for URLs, hooks, and (v3) **`infrastructure.routing`**; Traefik merge uses the **effective** manifest (see **[LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md)**) |
| No compose file yet | **`leco-app init --manifest-only`** (TTY) or LEco DevOps wizard to stub manifest + **`leco.yaml`** |

---

## Install

From the **local-ecosystem repository root**:

```bash
cd tools/deploy-cli
pip install -e .
leco-app --help   # equivalent: leco-devops --help
```

Requirements: **Python 3.11+**, **Docker** with Compose v2 (`docker compose`). Do **not** run `pip install` from `tools/` alone — only **`tools/deploy-cli/`** has `pyproject.toml`.

---

## Core files

| File | Role |
|------|------|
| **`leco.app.yaml`** | Bridge: **`lecoAppVersion`**, **`name`**, **`root`**, **`localHostProfile`**, optional **`configRefs`**, optional **`applicationVersion`**. Legacy v2 may still list compose / cloudflare / routing here; **v3** keeps those under **`leco.yaml`** → **`infrastructure`**. |
| **`leco.yaml`** (or **`localhost.yaml`**) | Profile: **`schemaVersion`**, **`archetype`**, **`urls[]`**, **`lifecycle`**, **`notes`**, and (v3) **`infrastructure`** (dockerCompose including **`additionalComposeFiles`**, cloudflare, routing, …) |
| **`config/leco-registry.yaml`** | Registry of apps LEco DevOps knows about (`id`, `label`, **`manifest`** path relative to repo root) |
| **`~/.local/share/leco/apps/<name>/`** | CLI state (override with `XDG_DATA_HOME`) |

Manifest versions **`lecoAppVersion: "2"`** and **`"3"`** use **`localHostProfile`** (or inline **`localhost`**). **v3** is recommended for new apps so infra stays in **`leco.yaml`**. See **[DEPLOY_CLI.md](DEPLOY_CLI.md)** for full field lists and **[LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md)** for merge rules, **`source`** symlinks, and LEco DevOps behavior.

---

## Typical workflows

### 1. New app with Docker Compose (local-ecosystem)

**Recommended — one command** after **`leco.app.yaml`** + **`leco.yaml`** exist (from **`init`** or hand-written):

```bash
cd /path/to/your/app
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
leco-app onboard       # compose up, leco-registry.yaml, merge routing.entries → traefik/dynamic.yml
```

Or combine init + onboarding:

```bash
leco-app init --onboard -E /path/to/local-ecosystem
```

**Step-by-step equivalent:** **`deploy`** → **`ecosystem-register`** → optionally **`ecosystem-register --merge-traefik`** (or paste output of **`traefik-fragment`**).

Put frontend/API containers on external network **`lh-network`** so Traefik can reach the hostnames in **`routing.entries`** (often compose **service** names or **container_name**). Details: **[DEPLOYMENT.md](DEPLOYMENT.md)** and **[DEPLOY_CUSTOM_APPS.md](DEPLOY_CUSTOM_APPS.md)**.

### 2. Cloudflare (Wrangler) application

If **`wrangler.toml`** (or **`cloudflare/wrangler.toml`**) exists, **`init`** can set **`cloudflare.wranglerConfig`** in the manifest. **Compose-only apps** (no `cloudflare` block) never trigger local KV/R2/D1. For Workers-backed apps:

| Step | Local KV/R2/D1 from wrangler |
|------|------------------------------|
| **`leco-app deploy`** | **Yes** by default after compose succeeds; use **`--no-provision-local-cf`** for compose-only. |
| **`ecosystem-register`** / **`onboard`** | **Yes** unless **`--no-provision-local-cf`**. |
| **`leco-app provision-local-cf`** | **Always** runs when wrangler path exists (manual repair / CI). |

Skips also apply when **`LECO_PROVISION_LOCAL_CF`** is `0`/`false`/`no`/`off`, or when the manifest sets **`cloudflare.provisionLocalResources: false`**. See **`tools/deploy-cli/README.md`** for extension points (new wrangler binding kinds).

**Wrangler bindings not mirrored** to local adapters today include browser rendering, queues, Durable Objects, Hyperdrive, etc.—use production Cloudflare or other local-ecosystem services as documented for those products.

Production deploys use:

```bash
leco-app cf-deploy --env staging
leco-app cf-deploy --env production --confirm-production
```

### 3. URLs, admin panels, CDN — `leco.yaml`

Use **`urls`** with **`role`** (`frontend`, `api`, `admin`, `backend`, `cdn`, `websocket`, `storybook`, `graphql`, `other`), **`label`**, and **`publicUrl`**. Optional **`internal`** documents compose service URLs (e.g. `http://php-fpm:9000`). These rows are primarily for **documentation, LEco DevOps display, and probes** unless you mirror them into **`routing.entries`** for Traefik.

### 4. Build / install before `deploy` — lifecycle hooks

Define commands under **`lifecycle.prepare`**, **`lifecycle.build`**, or **`lifecycle.preStart`** in **`leco.yaml`** (each step: **`command`**, optional **`cwd`**, **`shell`**, **`timeoutSec`**). Run from the app directory:

```bash
leco-app run-hooks --phase prepare
leco-app run-hooks --phase build
leco-app run-hooks --phase preStart
```

**Trust model:** these run **arbitrary shell commands** like `docker compose`. Only enable hooks in repositories you trust.

### 5. Traefik (`*.lh`)

**`leco-app onboard`**, **`init --onboard`**, and **`ecosystem-register --merge-traefik`** merge **`routing.entries`** from the **effective** manifest into **`traefik/dynamic.yml`** (atomic write; previous file copied to **`dynamic.yml.bak`**). In **v3**, **`infrastructure.routing`** normally lives in **`leco.yaml`**.

For a preview or manual merge only:

```bash
leco-app traefik-fragment -o /tmp/myapp-traefik.yml
```

**Hot reload:** Traefik’s file provider uses **`watch: true`** on **`dynamic.yml`**. After you save **`traefik/dynamic.yml`**, routes update **without restarting the Traefik container**. Restart Traefik only if you change **`traefik-static.yaml`** or volume mounts.

### 6. Stop stack and optionally unregister

```bash
leco-app down
# Remove from registry + strip Traefik keys (see DEPLOY_CLI.md / offload)
leco-app ecosystem-unregister <slug> --ecosystem-root /path/to/local-ecosystem
```

---

## LEco DevOps web UI

### Hosted apps tab

After **`ecosystem-register`** (or the wizard below), open **Hosted apps** for:

- Per-service metrics, logs, insights, health URL probes (from manifest).
- **Local profile** summary: archetype, **`leco.yaml`** URLs, lifecycle steps (read-only in the UI).
- Lifecycle actions via **Control** targets **`leco-stack-<id>`** (same token model as other Control actions). The LEco DevOps service runs **`leco-app deploy`**, **`stop`**, **`down`** (and **`down -v`** on reset) with **`--manifest`** for those stacks; **restart** / **recreate** / **pause** still use **`docker compose`** where LEco DevOps has no matching command. **Remove** / **Reset** always runs **offboard** (registry + hosting dirs + Traefik / local CF as configured) after **`down`**, even when **`down`** exits non-zero (e.g. missing compose file on disk).

### Routes tab

- Inspect **`traefik/dynamic.yml`** routers/services and registry overlap.
- **Load fragment from manifest** / **Load and merge** call **`leco-app traefik-fragment`** for a registry id, then optionally merge into **`dynamic.yml`** (atomic write + **`.bak`**). Same **control token** as other mutations.

### Register application (wizard)

On **Hosted apps**, expand **Register application**:

1. **App root path** — relative to the mounted repo (e.g. **`dashboard/subapp`**) or, for siblings exposed as **workspace-parent**, the prefix **`wsp:FolderName`** (no **`..`** in the path field). **Browse** uses read-only **`GET /api/leco/browse`** to pick a folder.
2. **Detect** — **`POST /api/leco/detect`** returns scan metadata plus, when present on disk, **`existing_manifest_yaml`** / **`existing_localhost_yaml`** (size-capped). The UI can load those into the editors or use generated previews. **Sample templates** come from **`GET /api/leco/register-samples`**.
3. **Generate YAML** / **Save YAML** (when needed) — **`POST /api/leco/generate-yaml`** or **`POST /api/leco/save-yaml`** with the control token. Read-only **`wsp:`** trees are **materialized** under **`hosting/app-available/<slug>/`** with a **`source`** symlink and **config symlinks** for **`configRefs`** / detected paths; **`Register`** is gated until YAML exists on disk (**`POST /api/leco/yaml-status`**).
4. Edit the optional YAML text areas if needed.
5. **Register** — **`POST /api/leco/register`** with the **control token** (same as **Control** tab). Writes or uses **`leco.app.yaml`** / **`leco.yaml`**, then runs **`leco-app ecosystem-register`** inside the LEco DevOps container (includes optional local KV/R2/D1 provision for Wrangler apps).

If **`DASHBOARD_CONTROL_TOKEN`** is set, you must configure the token in LEco DevOps before **Register** succeeds.

---

## Command cheat sheet

| Command | Purpose |
|---------|---------|
| **`leco-app onboard`** | Deploy + registry + Traefik merge (typical new-app flow) |
| **`leco-app init`** | Wizard: manifest + `leco.yaml` stub; **`--onboard -E …`** adds register + Traefik merge |
| **`leco-app init -y`** | Non-interactive defaults |
| **`leco-app init --manifest-only`** | Minimal manifest when no compose (TTY confirm) |
| **`leco-app detect`** | JSON: compose, Wrangler, archetype (scripts / LEco DevOps) |
| **`leco-app deploy` / `stop` / `down` / `status` / `logs`** | Compose lifecycle |
| **`leco-app run-hooks --phase <prepare\|build\|preStart>`** | Run merged sidecar profile lifecycle |
| **`leco-app traefik-fragment`** | Emit Traefik YAML snippet |
| **`leco-app ecosystem-register`** | Append/update **`leco-registry.yaml`** |
| **`leco-app ecosystem-unregister`** | **Local CF cleanup** (default), then **`docker compose down`**, then registry row + optional Traefik strip; **`--no-compose-down`** / **`--compose-volumes`** / **`--no-clean-local-cf`** |
| **`leco-app cf-deploy`**, **`cf-secrets-checklist`** | Wrangler deploy and secrets hints |

Full syntax, offload, and edge cases: **[DEPLOY_CLI.md](DEPLOY_CLI.md)**.

---

## Security and operations

- **Lifecycle hooks** and **compose** execute commands on the machine where leco runs — treat manifests like infrastructure code.
- **LEco DevOps registration** writes files and the registry only with a valid **control token** and **confined paths**.
- **Registry manifest paths** are stored **relative to the ecosystem repo**; the LEco DevOps container expects mounts documented in **`ecosystem-stack/services/dashboard.sh`** (**`/project`**, optional **`DASHBOARD_WORKSPACE_PARENT`**) so **`../other-repo/leco.app.yaml`** resolves inside the container.

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| App not in Hosted apps list | **`ecosystem-register`** run with correct **`LECO_ECOSYSTEM_ROOT`**; v3 apps need **effective** compose (e.g. **`infrastructure.dockerCompose`** in **`leco.yaml`**, not only on the bridge); rebuild/restart LEco DevOps after registry edits |
| Traefik 502 / no route | Containers on **`lh-network`**; **`traefik-fragment`** merged into **`dynamic.yml`**; hostnames match |
| **`detect`** / wizard path errors | Path must be under project or workspace-parent mount; no forbidden **`..`** traversal |
| Hooks fail | Run from manifest directory; check **`cwd`** in steps; increase **`timeoutSec`** |

---

## See also

- **[LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md)** — Bridge vs profile, hosting **`source`**, **`additionalComposeFiles`**, Wrangler vs **`wranglerBindingPreview`**, offboard semantics, code map.
- **[DEPLOY_CLI.md](DEPLOY_CLI.md)** — Technical reference: install, manifest tables, Traefik examples, offload, LEco DevOps API notes.
- **[DEPLOY_CUSTOM_APPS.md](DEPLOY_CUSTOM_APPS.md)** — Broader custom app routing and patterns.
- **`tools/deploy-cli/README.md`** — Package-oriented overview and resource model (local CF, state dir).

Open these from the LEco DevOps **Docs** tab when the repo is mounted at **`/project`**.
