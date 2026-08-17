# Hosted apps (dashboard & CLI)

Hosted apps are third-party applications registered in **`config/leco-registry.yaml`**, with manifests under **`hosting/app-available/<slug>/`**.

Open **Deploy ▾ → Hosted apps**.

## Hosted apps tab

| Action | What it does |
|--------|----------------|
| **Register application** | Opens the wizard: choose a **source** → Detect → YAML → Register |
| **Deploy** | `docker compose up -d --build` for `leco-stack-<slug>` |
| **Logs / metrics** | Runtime snapshot, log tail, insights |
| **Attached services** | Per-app inventory: data stores, runtimes, CF bindings, credentials, **host vs Docker DNS** connection strings, management UIs |
| **Seed data** | Discover `data/` dumps; **Import data** / dry-run (NDJSON stream, reimport) — not run at register |
| **Dev stack binding** | Attach the app to an isolated **Platform** dev stack (`platform.devStackId` in `leco.yaml`) |
| **Remove / Reset** | Full unregister: CF teardown → compose down → Traefik strip → delete hosting slot |

**Pending registration:** folders in `app-available/` with valid YAML but no registry row yet — finish **Register**.

## Two sources, one wizard

Expand **Register application (generate YAML · save · register)**. The first control is **SOURCE**:

| Source | Pick it when | What you give LEco |
|--------|--------------|--------------------|
| **Local folder** | The code is already on this machine — inside the repo, in a sibling repo mounted as `wsp:`, or at a host path | An **App root path** (typed, or picked with **Choose folder…** / **Browse…**) |
| **Git repository** | The code lives in Git and there is no checkout here — the normal case on a server, where there is no Finder | A **Repository URL**, optionally a branch/tag/commit and a credential |

**They converge immediately.** The Git path clones into a managed workspace and then *fills the App root path field* — from that point the two sources are the same wizard: **Detect → Generate YAML → Register → Deploy**.

### Local folder

Path forms accepted: repo-relative (`hosting/app-available/myapp`), **`wsp:SiblingRepo/subpath`** for a read-only sibling mount, or a full host path when the dashboard was started with path hints. No `..` traversal. Read-only trees are **materialized** under `hosting/app-available/<slug>/` with a `source` symlink — see [wsp: paths & materialize](help:onboarding-materialize).

### Git repository

Fields: **Repository URL** (`https://…` or `git@…`), **Branch, tag or commit** (blank = default branch), **Saved credential**, **Token or SSH private key**, and **Save this credential as** with a **Save credential** button. Two checkboxes: **Full history** (skip the shallow `--depth 1` clone — slower, needed by release tooling that reads past commits) and **On update, discard local changes** (hard-reset an existing clone to the requested ref).

- **Check repository** lists refs without cloning — use it to confirm credentials and the branch name first.
- **Clone / update & use** clones into **`hosting/app-sources/`** (gitignored) and fills **App root path**.
- Clones are shallow, wall-clock limited (**300 s** by default), and size-capped (**2048 MB**) — a runaway clone is killed and removed rather than left to fill the disk.
- Credentials go to `config/git-credentials.yaml` (mode `0600`, gitignored) and are handed to git through a temporary `GIT_ASKPASS` / `GIT_SSH_COMMAND` helper, so they never land in the clone's `.git/config`, a manifest, a log line, or a response. A URL that already embeds a token is refused — paste the token in the credential field instead.

Walkthrough: **[Git onboarding & CI/CD](help:git-cicd)**. Fuller onboarding story: **[Onboarding new apps](help:onboarding-overview)**.

### AI-assisted onboarding (optional)

A toggle in the wizard, showing the **active AI provider** as a pill and a **Configure AI provider →** link to **Service hubs**. When on, LEco streams an analysis of the detected tree and suggests configuration. Set the provider up first — see [Infrastructure tab](help:dash-infra) § *AI provider*.

## Register (CLI)

```bash
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem

# Materialized slot
leco-devops ecosystem-register -E "$LECO_ECOSYSTEM_ROOT" \
  --registry-manifest-relpath hosting/app-available/myapp/leco.app.yaml \
  --merge-traefik

# From the app directory after init
cd /path/to/myapp && leco-devops onboard -E "$LECO_ECOSYSTEM_ROOT"
```

## Control target

Each app becomes **`leco-stack-<id>`** on the **Control** tab (start, stop, down, deploy, staging, remove).

## Keep it deploying

Once an app is registered and answering, give it a pipeline on **Deploy ▾ → CI/CD** so a push redeploys it: `pull → build hook → deploy → verify → record`. See [Git onboarding & CI/CD](help:git-cicd).

## Staging vs remove

| Term | Meaning |
|------|---------|
| **Staging / offload** | `leco-devops offload` or Control **staging** — compose down, strip Traefik keys, **keep** `hosting/app-available/<slug>/` |
| **Remove / unregister** | Deletes the registry row and the hosting slot (when the manifest was under `hosting/`) |

There is **no** `hosting/app-staging/` directory.

## Zip upload

Dashboard or `POST /api/hosted/upload-zip` (control token) extracts into `hosting/app-available/<slug>/`, then **Detect** + **Register**. Use it when the machine has neither the folder nor Git access.

## Deep dives

- [Onboarding new apps](help:onboarding-overview)
- [Git onboarding & CI/CD](help:git-cicd)
- [wsp: & materialize](help:onboarding-materialize)
- [Hosting layout](help:hosting-layout)
- [Attached services panel](help:hosted-app-attached-services)
- [Seed data import](help:hosted-app-data-import)
- [Platform tab & dev stacks](help:dash-platform)
- [Overriding upstream apps](help:hosting-overrides)
- [Deploy & rebuild](help:deploy-rebuild)
- [Traefik routes](help:traefik-routes)

Back: [CLI basics](help:cli-basics)
