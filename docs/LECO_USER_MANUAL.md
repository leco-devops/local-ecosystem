# LEco DevOps — User manual

This is the operator's manual: what LEco DevOps is, how to get your first application onto a hostname, how to keep it deploying, how to let an AI agent drive it, and where to look when something breaks.

It is written to be read in order the first time. Everything deep lives in a canonical document; this page tells you which one and why.

| You want to… | Go to |
|---|---|
| Understand the parts | [What LEco DevOps is](#what-leco-devops-is) |
| Find your way around the UI | [The dashboard](#the-dashboard) |
| Install it | [Install](#install) |
| Put an app on a hostname | [Journey 1 — onboard an application](#journey-1--onboard-an-application) |
| Make a push redeploy it | [Journey 2 — keep it deploying (CI/CD)](#journey-2--keep-it-deploying-cicd) |
| Let Claude Code run it | [Journey 3 — point an AI agent at it](#journey-3--point-an-ai-agent-at-it) |
| Give LEco its own LLM | [Journey 4 — configure an AI provider](#journey-4--configure-an-ai-provider) |
| Run it on a real domain | [Real domains and TLS](#real-domains-and-tls) |
| Fix something | [When something breaks](#when-something-breaks) |
| Look up a command | [Command cheat sheet](#command-cheat-sheet) |

---

## What LEco DevOps is

**LEco DevOps** is a local DevOps platform. Three pieces, one machine:

| Piece | What it is |
|-------|------------|
| **The ecosystem stack** | Traefik edge routing, Postgres, Ollama, AirLLM, Open WebUI, n8n, Paperclip, Cloudflare-local adapters, an MCP server, optional infra add-ons and file transfer. Managed by `ecosystem-stack/ecosystem-stack.sh`. |
| **The dashboard** | The web UI and the API behind it. Everything that changes state goes through it. |
| **`leco-devops`** | The CLI for **hosted apps** — third-party applications you keep *outside* the ecosystem stack. (PyPI package name is `leco-app`; the command is `leco-devops`.) |

Two more entry points matter, and both act **through the dashboard API** rather than touching Docker themselves — which is why lifecycle rules and the control token live in one place no matter who is driving:

- **`leco-mcp`** — an MCP server, so an AI agent (Claude Code, Claude Desktop, an IDE assistant) can run LEco with audited, gated tools.
- **CI/CD** — a signed webhook from GitHub or GitLab triggers a deploy that is *verified* before it counts.

### What a hosted app looks like on disk

| File | Role |
|------|------|
| **`leco.app.yaml`** | **Bridge**: `lecoAppVersion`, `name`, `root`, `localHostProfile`, optional `configRefs`, `applicationVersion` |
| **`leco.yaml`** | **Profile**: `schemaVersion`, `archetype`, `urls[]`, `lifecycle`, `notes`, and (v3) `infrastructure` — `dockerCompose`, `cloudflare`, `routing`, `runtimes[]` |
| **`config/leco-registry.yaml`** | The registry of apps LEco knows about (`id`, `label`, `manifest` path relative to the repo root) |
| **`hosting/app-available/<slug>/`** | The app's hosting slot: manifests, optional `source` symlink, optional `docker-compose.leco-hosting.yml` overlay |
| **`~/.local/share/leco/apps/<name>/`** | CLI state (override with `XDG_DATA_HOME`) |

Use **`lecoAppVersion: "3"`** for new apps: infrastructure stays in `leco.yaml`, the bridge stays thin. Field-by-field reference: **[DEPLOY_CLI.md](DEPLOY_CLI.md)**. Merge rules, `source` symlinks, offboard semantics: **[LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md)**.

### When *not* to use `leco-devops`

| Goal | Use |
|------|-----|
| First-party stack services (Traefik, Ollama, n8n, …) | `ecosystem-stack` scripts and the **Control** tab |
| An isolated database/runtime per team on one machine | **Platform** tab → dev stacks — [help/03-platform-tab.md](help/03-platform-tab.md), [DEV_STACK_ISOLATION.md](DEV_STACK_ISOLATION.md) |
| A separate repo or folder with docker compose | `leco-devops` — this manual |
| Cloudflare Workers with Wrangler | `leco-devops` with `cloudflare.wranglerConfig` |

---

## The dashboard

Open **`https://localhost.lh`** (or `http://localhost:8090` before Traefik is up).

The navigation is **grouped**. Four items open a dropdown:

| Nav | Contains | Use it to |
|-----|----------|-----------|
| **Overview** | — | See health, probes, and hosted app URLs at a glance |
| **Deploy ▾** | **Hosted apps** · **CI/CD** · **Routes** | Put an application on a hostname and keep it there |
| **Operate ▾** | **Control** · **Infrastructure** | Start/stop services, health, model managers, Docker inventory |
| **Insight ▾** | **Metrics** · **Logs** · **Reference** | Host metric history, log tail, the `*.lh` URL encyclopedia |
| **Platform ▾** | **Platform** · **MCP** | Platform config and dev stacks; the AI-agent bridge |
| **Help** | page `/help` | The guided manual, searchable |
| **Service hubs** | page `/hub` | Per-service ops pages, **UI credentials**, **AI providers** |

**Docs** (`/?tab=docsTab`) and **Develop** are no longer in the nav — reach them from the page footer or a direct URL such as `/?tab=docsTab&doc=production-hardening`.

**Reference** is the URL encyclopedia, not the Docs tab renamed. They have always been different things.

### The control token

When `DASHBOARD_CONTROL_TOKEN` is set in `ecosystem-stack/services/dashboard.sh`, every mutating action needs it. Enter it once on **Control**; it is reused by Hosted apps, CI/CD, Routes, Platform, and the AI providers panel.

When it is **not** set, the control API is open. That is fine on a laptop and unacceptable anywhere else — see [Real domains and TLS](#real-domains-and-tls).

---

## Install

From the **repository root**:

```bash
# 1 · the stack
./ecosystem-stack/ecosystem-stack.sh start

# 2 · the CLI (Python 3.11+, Docker with Compose v2)
cd tools/deploy-cli && pip install -e . && leco-devops --help
```

Do **not** `pip install` from `tools/` — only `tools/deploy-cli/` has a `pyproject.toml`.

DNS and local TLS are prerequisites, not optional polish. Full first-machine setup: **[SETUP.md](SETUP.md)**; the short version is in [Local TLS](#local-tls-lh).

---

## Journey 1 — onboard an application

**Goal:** an application you did not write is reachable at `https://myapp.lh`, visible in the dashboard, and controllable from it.

### Step 1 — decide where the code comes from

The Register wizard starts from **two sources**, and the only question is how the code reaches this machine:

| Source | Pick it when |
|--------|--------------|
| **Local folder** | The tree is already here — in the repo, in a sibling repo mounted as `wsp:`, or at a host path |
| **Git repository** | The code lives in Git and there is no checkout here — the normal case on a server, where there is no Finder to browse with |

**They converge at the App root path.** *Clone / update & use* clones the repository and writes the resulting path into the same field the local-folder branch fills by hand. Everything after that is identical.

### Step 2 — Register application

**Deploy ▾ → Hosted apps → Register application (generate YAML · save · register)**.

**If Local folder:** type or **Browse…** to the path. Repo-relative (`hosting/app-available/myapp`), `wsp:SiblingRepo/subpath`, or a host path. No `..` traversal. A read-only tree is **materialized** into `hosting/app-available/<slug>/` with a `source` symlink and config symlinks for `configRefs`, each `runtimes[].config`, and any `wrangler.*.toml` found — see [help/12-onboarding-materialize.md](help/12-onboarding-materialize.md).

**If Git repository:** paste the URL, optionally a branch/tag/commit.

- **Check repository** lists refs *without cloning* — do this first; a bad credential or branch name surfaces immediately.
- **Clone / update & use** clones into **`hosting/app-sources/<id>/`** (gitignored). Shallow `--depth 1` unless you tick **Full history**; killed and cleaned up if it exceeds **300 s** or **2048 MB** (`LECO_GIT_TIMEOUT`, `LECO_GIT_MAX_CLONE_MB`).
- Private repos take an HTTPS token or an OpenSSH key. Credentials go to `config/git-credentials.yaml` (mode `0600`, gitignored) and are passed to git through a temporary `GIT_ASKPASS` / `GIT_SSH_COMMAND` helper — never into the clone's `.git/config`, a manifest, a log, or a response. A URL that already embeds a token is refused.

### Step 3 — Detect, generate, register

1. Set **App id (slug)** and **Label**.
2. **Detect** — scans compose, every `wrangler.*.toml`, ports, archetype; previews the YAML it would write.
3. **Generate YAML** / **Save YAML** — writes `leco.app.yaml` + `leco.yaml`. **Register** stays disabled until both exist on disk.
4. **Public URLs** — edit each `urls[]` entry; **Write into profile YAML** before saving.
5. **Register** — runs `leco-devops ecosystem-register` inside the dashboard container: registry row, optional local KV/R2/D1 provisioning for Wrangler apps, Traefik merge. **Deploy stack** (on by default when the manifest has `dockerCompose`) then runs `leco-devops deploy`.

Optional: the **AI-assisted onboarding** toggle streams an analysis of the detected tree. It uses whatever provider you configured in [Journey 4](#journey-4--configure-an-ai-provider).

### The same thing from the CLI

```bash
cd /path/to/your/app
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
leco-devops onboard          # compose up → register → Traefik merge
```

Or `leco-devops init --onboard -E /path/to/local-ecosystem` to scaffold the manifests first. Step-by-step equivalent: `deploy` → `ecosystem-register --merge-traefik`.

### Step 4 — verify it actually answers

Registration is not success; a hostname that returns 200 is.

```bash
curl -kIsS https://myapp.lh/            # through Traefik
```

On **Hosted apps**, the app's row shows a live probe. If it is red:

- Are the containers on the external network **`lh-network`**? Traefik cannot reach anything else.
- Does a router exist for the hostname? Check **Deploy ▾ → Routes**.
- Does the `loadBalancer` host match the real container name (`container_name`, or `{project}-{service}-1`)?

Full decision table: **[HOSTED_APPS_TRAEFIK_RUNBOOK.md](HOSTED_APPS_TRAEFIK_RUNBOOK.md)** and [help/09-502-routing.md](help/09-502-routing.md).

### Step 5 — wire it up

- **Attached services** — the app's data stores, runtimes, and Cloudflare bindings, with both **host** (`127.0.0.1` / `*.lh`) and **Docker DNS** connection strings. Use the *host* port from here in a GUI client; use the *Docker DNS* form inside compose. See [help/12-hosted-app-attached-services.md](help/12-hosted-app-attached-services.md).
- **Seed data** — if `hosting/app-available/<slug>/data/` exists, **Import data** (or a dry-run plan) loads it. It is **never** run at register time. See [help/13-hosted-app-data-import.md](help/13-hosted-app-data-import.md).
- **Dev stack binding** — set `platform.devStackId: <stackId>` in `leco.yaml` to attach the app to an isolated Platform stack's Postgres/MySQL.
- **Lifecycle hooks** — `lifecycle.prepare` / `build` / `preStart` in `leco.yaml`, run with `leco-devops run-hooks --phase <phase>`. **These execute arbitrary shell commands.** Only enable them in repositories you trust.

### Taking it back down

| Action | Effect |
|--------|--------|
| `leco-devops down` | Stop the compose stack, keep everything else |
| **Staging / offload** | Compose down, strip Traefik keys, **keep** `hosting/app-available/<slug>/` |
| **Remove / Reset** (Hosted apps) | Local CF teardown → `docker compose down` (`-v` on Reset) → Traefik strip → registry row → hosting slot |
| `leco-devops ecosystem-unregister <slug> -E …` | The CLI equivalent |

There is no `hosting/app-staging/` directory. Details: [help/12-deploy-rebuild.md](help/12-deploy-rebuild.md).

---

## Journey 2 — keep it deploying (CI/CD)

**Goal:** pushing to the repository redeploys the app, and a deploy that leaves the app broken is *recorded as broken*.

Open **Deploy ▾ → CI/CD**.

### Create the pipeline

**New pipeline** takes: **Application (registry id)**, **Repository URL**, **Branch to deploy**, **Git host** (*Auto*, GitHub only, GitLab only, or Generic signed), an optional **Verify URL**, an optional **Build/test hook**, and **Auto-deploy on push**.

One pipeline per application.

### Install the webhook

Copy the **webhook URL** and the **secret**. **The secret is shown once** — copy it before you dismiss the panel; if you lose it, use **Rotate secret**.

- **GitHub** → *Settings → Webhooks*, content type `application/json`, paste the secret. GitHub signs the raw body as `X-Hub-Signature-256`.
- **GitLab** → *Settings → Webhooks*, paste the secret into *Secret token*. GitLab sends it verbatim in `X-Gitlab-Token`; it does not sign the body.

On a real domain, set `LECO_PUBLIC_BASE_URL=https://leco.example.com` on the dashboard first — otherwise the copied URL says `localhost:8090` and no Git host can reach it.

### What a run does

```
pull → build hook (optional) → deploy → verify → record
```

- **pull** checks out the exact pushed commit.
- **build hook** runs a **compose service your app defines** — not an arbitrary command string. That is deliberate: a command in a webhook-reachable config would be host code execution behind one leaked secret.
- **deploy** is the same `leco-devops deploy` the dashboard runs.
- **verify** is a **real HTTP probe** of the app's public URL, retried up to six times.
- **record** stores the commit, per-step status, and the captured log.

**A deploy that finishes while the app returns 502 is a FAILED run**, and the last-deployed commit is **not** advanced. That is the whole point: it is what makes **Rollback** point at a commit that actually served traffic.

If verify reports *skipped*, no verify URL was configured or derivable — set one, or a broken deploy will be recorded as a success.

### Rollback

**Rollback** redeploys the previous good commit. It **does not** migrate a database backwards, and it does not undo anything the app did to its own data. Treat it as "put the old code back", nothing more.

### Behaviours worth knowing

- A push to a branch the pipeline does not track is acknowledged and ignored.
- Ten rapid pushes produce **one** run, not ten.
- An unsigned or wrongly-signed request gets a **403** and changes nothing — and the response is identical for every cause, so it will not tell you which. Diagnosis: [help/09-troubleshooting.md](help/09-troubleshooting.md).

Operator guide: **[help/21-git-and-cicd.md](help/21-git-and-cicd.md)**. Full reference: **[GIT_AND_CICD.md](GIT_AND_CICD.md)**.

---

## Journey 3 — point an AI agent at it

**Goal:** Claude Code (or another MCP client) can inspect and operate this platform, with a record of everything it did.

Open **Platform ▾ → MCP**.

1. **1 · Server status** tells you whether `leco-mcp` is installed, running, reachable, how many tools it exposes, which safety gates are on, and where the activity log is.
2. **2 · Install on an agent** gives you copyable commands for the three ways to connect:
   - **Claude Code plugin** — the fastest path; it bundles the skill, the server, and the slash commands.
   - **stdio** — the agent launches the process; best for a single machine.
   - **HTTP** — `https://mcp.lh/mcp`; best for agents elsewhere on the LAN, or ones that cannot spawn processes.
   Then run `leco-mcp doctor` to confirm the endpoint, the tool list, and the log path.
3. **3 · Plugin commands** lists every `/leco:*` command, read from the plugin on disk so the table cannot drift.

Once an agent is connected, the rest of the tab is the audit trail: **Connected agents (sessions)**, **Applications — what MCP is doing to them**, the **Activity log** (every call, with filters for *blocked only* and *errors only*), and **Tool usage**.

**`blocked` is not a failure.** It means a safety gate refused the call — destructive tools and credential access are opt-in and off by default. `error` is the different case: a tool that ran and failed.

Operator guide: **[help/20-mcp-server.md](help/20-mcp-server.md)**. Full tool tables, transports, and gates: **[MCP_SERVER.md](MCP_SERVER.md)**.

---

## Journey 4 — configure an AI provider

**Goal:** LEco itself can call an LLM, for AI-assisted onboarding and other AI-assisted tasks.

Open **Service hubs → AI providers (LLM access)** (`/hub#hub-ai-providers`).

### Pick a provider

| Provider | Notes |
|----------|-------|
| **No AI (deterministic only)** | The default; everything still works, just without suggestions |
| **Ollama (local)** · **AirLLM (local large models)** | Nothing leaves the machine |
| **Anthropic (Claude API)** · **Google (Gemini API)** · **OpenAI** | Direct first-party APIs |
| **Aggregator / OpenAI-compatible** | Eden AI, OpenRouter, Groq, Together, DeepSeek, Mistral, xAI, Cerebras, NVIDIA NIM, and self-hosted gateways (LiteLLM, vLLM, LM Studio, LocalAI) — pick the aggregator, then the **Service**, and it resolves its own base URL |
| **Hybrid (local SLM + cloud LLM)** | A local model does the bulk; a cloud model handles what it cannot. Holds **two** providers and **two** model ids. |

### Connect, then choose a model

1. Fill only the fields the provider needs — **Service**, **Base URL**, **API key**, **Hybrid pair**, **Request timeout**.
2. **Connect & list models** — LEco calls the provider and lists its **real** catalogue. Filter by name; sort by *Capability tier*, *Name*, *Context window*, or *Cost*.
3. Click a model to fill **Selected model id**.
4. **Save configuration** — active immediately, no restart.

> **Never type a model id from memory.** An Anthropic provider paired with `gpt-4o-mini` authenticates perfectly and then returns **HTTP 404** on the first real call, because that model does not exist there. This is exactly what the *Connect & list models* step prevents. Watch for it in **Hybrid** mode especially, where a stale *cloud* model id can survive a change of cloud vendor.

### Where the key goes

Credentials are written server-side only, to **`config/ai-providers.yaml`** (mode `0600`, gitignored). They are never written into the repository, a manifest, `leco.app.yaml`, a log line, or any API response — the browser only ever receives a mask such as `sk-a••••••••••••x9f2`. The key field is **never prefilled**: leave it blank to keep the stored key, or press **Remove stored key** to delete it on the next save.

The chosen provider is mirrored read-only on **Infrastructure → 8 · AI-assisted onboarding**, and drives the AI toggle in the Register wizard.

### Ask LEco — grounded answers about this platform, API only

There is a retrieval-augmented question API at **`/api/ai/rag/*`**. It indexes the repository's own documentation (`docs/**`, the top-level guides, the Claude plugin skills) and can mix in **live state** from this machine — stack status, services, control targets, hosted apps, Traefik routes, Cloudflare-local status, the AI config with keys masked, and service or per-app logs — then answers with numbered citations back to the files it used.

**There is no UI for it.** Nothing in the dashboard calls these endpoints today. The only way to use it is an HTTP client:

```bash
# What is indexed, how fresh, and which provider would answer
curl -s http://localhost:8090/api/ai/rag/status

# Ask (add -H "X-Control-Token: $DASHBOARD_CONTROL_TOKEN" when a token is set)
curl -s http://localhost:8090/api/ai/rag/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"why is my app returning 502 through Traefik?","app":"myapp","top_k":6}'

# Retrieval only — never contacts a provider; good for checking the corpus
curl -s http://localhost:8090/api/ai/rag/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"where are UI credentials stored","retrieval_only":true,"live":false}'
```

`POST /api/ai/rag/ask/stream` returns NDJSON (`status` → `sources` → `token`… → `done`). `POST /api/ai/rag/reindex` rebuilds the index and can opt into local embeddings. `GET /api/ai/rag/status` needs no token; the other three respect the control token. Pin live sources with `"live": ["traefik_routes","app_logs"]`, or disable them with `"live": false`.

The index rebuilds when a document's mtime changes, so editing a file under `docs/` is picked up without restarting anything.

---

## Operating the platform

### Control — start and stop things

**Operate ▾ → Control**. Cards are grouped: *Bulk & orchestration*, *Ecosystem stack & Traefik*, *Infra add-ons & file transfer*, *Cloudflare local*. Each service also carries a **default policy** — `start` (included in bulk), `stop` (skipped by a bulk start), or `offloaded` (excluded from all bulk automation).

Bulk `stop` / `restart` / `redeploy` skip the dashboard itself and, by default, **Traefik** and **Postgres**, so routing and the shared database stay up. Override with `ECOSYSTEM_BULK_PLATFORM_SKIP`.

Registered apps appear here as `leco-stack-<id>` control targets. Details: [help/03-control.md](help/03-control.md).

### Infrastructure — health and models

**Operate ▾ → Infrastructure**. Jump bar: *Health · Services · Trends · CF local · Ollama · AirLLM · AI · Inventory*. Sections 5 and 6 hold the purple **Model manager** panels (Install / Load / Unload / Remove / Show CLI); section 7 is Paperclip; section 8 mirrors the AI provider. Details: [help/03-infrastructure.md](help/03-infrastructure.md).

### Platform — dev stacks and platform config

**Platform ▾ → Platform** edits `config/leco-platform.yaml` and builds **isolated dev stacks** under `platform/dev-stacks/<id>/` — WordPress, Magento, Laravel, plain Postgres/Redis, and so on, each its own compose project.

| Action | Use it when |
|--------|-------------|
| **Start** / **Stop** | Ordinary lifecycle; Stop keeps volumes |
| **Repair** | 502, wrong image name, missing route — fixes config in place, keeps data and manual edits |
| **Reinstall** | Wrong DB major version, corrupt install — regenerates and wipes volumes |
| **Destroy** | Remove the stack, its volumes, its directory, and its routes |

```bash
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
leco-devops platform presets
leco-devops dev-stack create wordpress --preset wordpress --sample-data
leco-devops dev-stack start wordpress --stream
leco-devops dev-stack repair magento-full
```

Details: [help/03-platform-tab.md](help/03-platform-tab.md) and [DEV_STACK_ISOLATION.md](DEV_STACK_ISOLATION.md).

### Routes — Traefik

**Deploy ▾ → Routes** shows merged routers and services, registry overlap, a quick route builder, and a **Merge YAML fragment** panel that calls `leco-devops traefik-fragment` for a registry id and merges the result into `hosting/traefik/dynamic.yml` (atomic write plus a `.bak`).

Two files, two meanings:

| File | Meaning |
|------|---------|
| `traefik/dynamic.yml` | Platform stack routes, in git. Copied to `hosting/traefik/01-stack-core.yml` every time Traefik starts. |
| `hosting/traefik/dynamic.yml` | The merge target for hosted apps. Traefik's file provider **watches this directory**, so a merge usually applies without a restart. |

Restart Traefik after changing `traefik/dynamic.yml`, the static config, or volume mounts. If routes vanish or everything 404s, run `ecosystem-stack/services/traefik.sh heal`.

### Service hubs — credentials and per-service pages

`/hub` gives each service a single operations page: what it does, live Docker status, credentials, connection strings, and every URL you can open. **UI access (local dev)** holds logins for MinIO, Adminer, n8n, Open WebUI, and SFTP/FTP, with short-lived **Auto-login** magic links. See [UI_CREDENTIAL_VAULT.md](UI_CREDENTIAL_VAULT.md) and [help/12-file-transfer.md](help/12-file-transfer.md).

---

## Real domains and TLS

### Local TLS (`*.lh`)

Generate the local certificate with the repo script:

```bash
./certs/generate-certs.sh
./ecosystem-stack/ecosystem-stack.sh restart traefik
```

It discovers every `*.lh` hostname Traefik and the registry actually serve, writes **one explicit SAN per hostname**, and verifies coverage before finishing. Re-run it after adding a hosted app with a new hostname. `--list` previews without writing.

> **Do not run `mkcert "*.lh"`.** A wildcard directly below a top-level domain is rejected by RFC 6125 and the CA/Browser Forum rules, because `*.lh` would claim an entire TLD. Such a certificate matches **nothing** — not even `dashboard.lh`. The chain still verifies, so `mkcert -install` looks like it worked and the browser still says *Not secure*. Wildcards remain legal one level deeper: `*.myapp.lh` is fine.

mkcert is local-only. Its CA exists solely in the trust store of the machine that installed it.

### Going to a real domain

Three keys in `config/leco-platform.yaml` decide everything:

```yaml
deployment_mode: local   # or: cloud
base_domain: lh          # or: a domain you own
tls:
  mode: mkcert           # or: acme | cloudflare | static
```

| `tls.mode` | Who issues certificates | Use when |
|------------|-------------------------|----------|
| `mkcert` | A CA trusted only on this machine | Local `.lh` development |
| `acme` | Let's Encrypt via Traefik (HTTP-01 on the `web` entrypoint) | Public DNS resolves to the VM and TCP 80 reaches Traefik |
| `cloudflare` | Cloudflare at its edge; origin uses an Origin Certificate or a Tunnel | Behind Cloudflare — [CLOUDFLARE_SSL_INSTALL.md](CLOUDFLARE_SSL_INSTALL.md) |
| `static` | You do — operator-supplied PEM files | You already have certificates |

`certs/generate-certs.sh` refuses to run in the non-mkcert modes and explains who issues certificates instead.

> ### Read this before you expose anything
>
> **[PRODUCTION_HARDENING.md](PRODUCTION_HARDENING.md)** enumerates the defaults that make a laptop pleasant and a server dangerous: the **control API is unauthenticated unless you opt in**, the **Traefik API runs `insecure: true`**, **every published port binds `0.0.0.0`**, `cloud-install.sh` performs no hardening, local-development credentials would ship as-is, and the MCP server has its own exposure story. Each finding comes with the fix and a pre-flight checklist.
>
> Do this before DNS points at the machine, not after.

Cloud VM install: [CLOUD_VM_DEPLOYMENT.md](CLOUD_VM_DEPLOYMENT.md) · [help/12-cloud-vm-deployment.md](help/12-cloud-vm-deployment.md).

---

## When something breaks

Start with **[help/09-troubleshooting.md](help/09-troubleshooting.md)** — it now covers webhook 403s, verify failures, clone timeouts and private-repo auth, model-id mismatches, and the `*.lh` certificate trap. Then:

| Symptom | Look at |
|---------|---------|
| App not in the Hosted apps list | `ecosystem-register` run with the right `LECO_ECOSYSTEM_ROOT`; v3 apps need compose in the **effective** manifest (`infrastructure.dockerCompose` in `leco.yaml`), not only on the bridge |
| Traefik **502** / no route | Containers on `lh-network`; `docker-compose.leco-hosting.yml` + `additionalComposeFilesFromManifest`; fragment merged into `hosting/traefik/dynamic.yml`; `loadBalancer` host matches the container name. **[HOSTED_APPS_TRAEFIK_RUNBOOK.md](HOSTED_APPS_TRAEFIK_RUNBOOK.md)** |
| Global **404** on every host | `traefik.sh heal` / `ensure-hosting-files` — usually an empty or invalid `http` block in the runtime file provider |
| Varnish **503 Backend fetch failed** after restart | Server still starting or crash-looping; use the `sample-node-varnish-multiprocess` template. [help/09-503-varnish-backend.md](help/09-503-varnish-backend.md) |
| URL column shows **HTTP 0**, or API works but UI does not | Restart the dashboard after upgrades; probes use `http://traefik` for `*.lh`. The browser's API base must be `https://<app>.lh/api`, not `localhost` |
| CI/CD webhook returns **403** | Signature mismatch — rotate the secret, check content type, check GitHub vs GitLab |
| CI/CD run fails at **verify** | The check working: the deploy finished, the app did not answer. Fix the app |
| Clone hangs or fails | Shallow by default; timeout and size guards; private repos need a token or key in the credential field, never in the URL |
| AI provider returns **404** | Model id from another vendor — use **Connect & list models** |
| Browser says **Not secure** on `.lh` | The old `*.lh` wildcard certificate. Run `./certs/generate-certs.sh` |
| Detect / wizard path errors | Path must be under the project or workspace-parent mount; no `..` traversal |
| Hooks fail | Run from the manifest directory; check `cwd`; raise `timeoutSec` |
| Seed import failed / wrong port | Use the **host** port from **Attached services** (e.g. `27018`). [help/13-hosted-app-data-import.md](help/13-hosted-app-data-import.md) |

---

## Command cheat sheet

```bash
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
```

| Command | Purpose |
|---------|---------|
| `leco-devops onboard` | Deploy + registry + Traefik merge (the typical new-app flow) |
| `leco-devops init` | Wizard: manifest + `leco.yaml` stub. `--onboard -E …` adds register + merge; `-y` for defaults; `--manifest-only` when there is no compose |
| `leco-devops detect` | JSON: compose, Wrangler, archetype |
| `leco-devops deploy` / `stop` / `down` / `status` / `logs` | Compose lifecycle |
| `leco-devops run-hooks --phase prepare\|build\|preStart` | Run merged profile lifecycle steps |
| `leco-devops traefik-fragment -o file.yml` | Emit a Traefik YAML snippet |
| `leco-devops ecosystem-register [--merge-traefik]` | Append/update `leco-registry.yaml` |
| `leco-devops ecosystem-unregister <slug>` | Local CF cleanup → `compose down` → registry row → optional Traefik strip |
| `leco-devops scaffold <id> --template … --source-path …` | Copy a `hosting/samples/` pack into a hosting slot |
| `leco-devops cf-deploy --env staging` | Wrangler deploy (`--confirm-production` for production) |
| `leco-devops platform presets` · `dev-stack create/start/repair/reinstall` | Platform and dev stacks |
| `./ecosystem-stack/ecosystem-stack.sh start\|stop\|restart\|heal <svc>` | Stack services |
| `./leco-cli.sh stack status` | Stack overview |
| `./certs/generate-certs.sh [--list]` | Local `.lh` certificate |
| `leco-mcp doctor` | Check the MCP server end to end |

Full syntax and edge cases: **[DEPLOY_CLI.md](DEPLOY_CLI.md)**.

### Cloudflare (Wrangler) applications

If `wrangler.toml`, `infra/wrangler.*.toml`, or `cloudflare/wrangler.toml` exists, `init` / **Detect** can set `cloudflare.wranglerConfig` and one `infrastructure.runtimes[]` entry per Worker config. Local KV/R2/D1 are provisioned by `deploy`, `ecosystem-register`, and `onboard` unless you pass `--no-provision-local-cf`, set `LECO_PROVISION_LOCAL_CF=0`, or set `cloudflare.provisionLocalResources: false`. `leco-devops provision-local-cf` always runs when a wrangler path exists.

Queues, Durable Objects, and Vectorize have **no** local adapter today; Browser Rendering, Hyperdrive, and Email Routing have partial substitutes. Matrix and roadmap: **[CF_LECO_SERVICE_MAP.md](CF_LECO_SERVICE_MAP.md)**.

---

## Security notes

- **Lifecycle hooks and compose execute commands on this machine.** Treat manifests as infrastructure code and only enable hooks for repositories you trust.
- **Registration writes files and the registry only with a valid control token and confined paths.** Registry manifest paths are stored relative to the ecosystem repo; the dashboard container expects the mounts documented in `ecosystem-stack/services/dashboard.sh` (`/project`, optional `DASHBOARD_WORKSPACE_PARENT`).
- **Secrets are file-scoped and gitignored**: `config/ai-providers.yaml`, `config/git-credentials.yaml`, `config/ui-credentials.yaml`, all mode `0600`, all masked in every API response.
- **The CI/CD webhook is authenticated by its HMAC signature and nothing else.** Do not add a control-token check in front of it — the Git host cannot send one, and it would silently break every webhook while looking like an improvement.
- **MCP destructive tools and credential access are opt-in**, gated by `LECO_MCP_ALLOW_DESTRUCTIVE` / `LECO_MCP_ALLOW_CREDENTIALS`, and every call is written to the activity log.

---

## See also

| Document | For |
|----------|-----|
| **[Help & User Manual](help/00-welcome.md)** (`/help` in the dashboard) | The guided, searchable version of this material |
| [LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md) | Bridge vs profile, `source` symlinks, offboard semantics, code map |
| [DEPLOY_CLI.md](DEPLOY_CLI.md) | Command and YAML field reference |
| [DEPLOY_CUSTOM_APPS.md](DEPLOY_CUSTOM_APPS.md) | Broader custom-app routing patterns |
| [GIT_AND_CICD.md](GIT_AND_CICD.md) | Pipelines, signatures, runs, rollback |
| [MCP_SERVER.md](MCP_SERVER.md) | MCP transports, tools, gates |
| [PRODUCTION_HARDENING.md](PRODUCTION_HARDENING.md) | Everything to close before going public |
| [ARCHITECTURE.md](ARCHITECTURE.md) · [HLD.md](HLD.md) · [LLD.md](LLD.md) | Design context |
| [DEVELOPMENT_PLAYBOOK.md](DEVELOPMENT_PLAYBOOK.md) | Maintainer daily commands |

Open any of these from the dashboard footer, or at `/?tab=docsTab`.
