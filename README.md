<h1 align="center">LEco DevOps Open Project</h1>

<p align="center">
  <strong>One-click deploy for almost any application — no manual rewiring</strong><br />
  LEco reads your repo, converts config, orchestrates Compose &amp; Traefik, and ships on <code>*.lh</code>
</p>

<p align="center">
  <a href="#start-in-minutes"><strong>Get started</strong></a>
  &nbsp;·&nbsp;
  <a href="#platform"><strong>Platform</strong></a>
  &nbsp;·&nbsp;
  <a href="#features"><strong>Features</strong></a>
  &nbsp;·&nbsp;
  <a href="#use-cases"><strong>Use cases</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/PROJECT.md"><strong>Technical guide</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/leco-devops/local-ecosystem"><strong>Source</strong></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" />
  <img src="https://img.shields.io/badge/stack-Docker-2496ED?logo=docker&logoColor=white" alt="Docker" />
  <img src="https://img.shields.io/badge/routing-Traefik-24A1C1" alt="Traefik" />
  <img src="https://img.shields.io/badge/community-open%20source-brightgreen" alt="Open source" />
</p>

---

<p align="center">
  <strong>LEco DevOps</strong> is a free, MIT-licensed local cloud edge: point it at an app repo, and it does the heavy lifting —<br />
  detect existing Docker Compose, Wrangler, and env layout · generate LEco manifests · merge Traefik routes · deploy.
</p>

---

## What makes LEco different

<p align="center">
  <strong>Our USP: deploy first, configure later.</strong><br />
  You should not have to rewrite every app for a new platform. LEco <em>reads</em> what you already have,<br />
  <em>converts</em> it into LEco manifests and hosting slots, <em>orchestrates</em> the stack, and <em>deploys</em> — often in one click from the dashboard or <code>leco-devops onboard</code>.
</p>

| Step | What LEco does |
|------|----------------|
| **Read** | Scans `docker-compose.yml`, `wrangler.toml`, `.env`, Dockerfiles, and project layout from your Git tree |
| **Convert** | Builds `leco.app.yaml` + `leco.yaml`, materializes `hosting/app-available/<slug>/`, symlinks config without touching upstream |
| **Orchestrate** | Wires Compose projects, `lh-network`, Traefik host rules, optional Cloudflare-local bindings |
| **Deploy** | `compose up`, registry entry, route merge — reachable at `https://<your-app>.lh` with no hand-edited port maps |

Works for **Docker Compose apps**, **React/Vue + API splits**, **Workers + Wrangler**, and **preset CMS/framework stacks** — the same operator experience whether the source is your monorepo or a third-party checkout.

---

## Platform

The **Platform** tab is a first-class capability — not just settings. It is how you run **local or cloud VM** deployments, **curated service bundles**, and **isolated dev stacks** from one UI (mirrored by `leco-devops platform` and `leco-devops dev-stack` on the CLI).

| Capability | What it does for you |
|------------|----------------------|
| **Platform settings** | `config/leco-platform.yaml` — deployment mode, `base_domain`, TLS (mkcert / ACME / static / Cloudflare), which ecosystem services are enabled |
| **Ecosystem bundles** | Start or stop groups of stack services (Traefik, Postgres, AI, Cloudflare-local, …) without memorizing script order |
| **Dev stack builder** | One-click **WordPress**, **Magento**, **Laravel**, LAMP/MEAN, or custom component picks — each stack is its own Compose project and network |
| **Stack lifecycle** | **Start**, **Stop**, **Repair**, **Reinstall**, **Destroy** with live logs — fix routing in place or wipe and regenerate from template |
| **Cloud VM path** | Selective install on a Linux VM with real domains and external LLM providers — same Platform model as your laptop |
| **Bind hosted apps** | Point `platform.devStackId` at a stack so a LEco-hosted app shares DB/redis endpoints without port conflicts |

Presets and versions live in **`ecosystem-stack/config/dev-stack-presets.yaml`** and **`component-catalog.yaml`**. Generated stacks land under **`platform/dev-stacks/<id>/`** with Traefik routes in **`hosting/traefik/20-dev-stacks.yml`**.

<p align="center">
  <a href="docs/help/03-platform-tab.md"><strong>→ Platform tab guide</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/DEV_STACK_ISOLATION.md"><strong>→ Dev stack isolation</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/CLOUD_VM_DEPLOYMENT.md"><strong>→ Cloud VM deployment</strong></a>
</p>

---

## Features

### Edge & networking

| Capability | What it does for you |
|------------|----------------------|
| **Traefik reverse proxy** | One entry on ports 80/443; route by hostname instead of `:8080`, `:3000`, … |
| **`*.lh` local DNS** | Stable URLs like `https://n8n.lh` and `https://dashboard.lh` that match how teams talk about services |
| **mkcert TLS** | Trusted HTTPS in the browser without fighting self-signed warnings |
| **Network repair** | `repair-network` and dashboard heals re-attach `lh-network` when containers drift |

### Operations dashboard

| Capability | What it does for you |
|------------|----------------------|
| **LEco DevOps UI** | Single pane for stack status, metrics, logs, in-app docs, and service control |
| **Control tab** | Start, stop, restart, and repair ecosystem services without memorizing shell scripts |
| **Hosted apps** | Register third-party repos, **Detect → Generate YAML → Register → Deploy** in the UI, probe URLs, manage lifecycle |
| **One-click onboarding** | `leco-devops onboard` / **init --onboard** — deploy, registry, and Traefik merge in one flow |
| **Built-in help** | Operator and developer manuals served from the same UI you run the stack with |

### Application toolchain

| Capability | What it does for you |
|------------|----------------------|
| **`leco-devops` CLI** | `detect`, `init`, `onboard`, scaffold, platform, and dev-stack commands — same brain as the dashboard |
| **Hosted app slots** | Keep upstream repos clean; overrides and symlinks live in `hosting/app-available/<slug>/` |
| **Isolated dev stacks** | Per-team or per-CMS Compose projects (`platform/dev-stacks/`) with their own DB/network |
| **Manifest binding** | Attach a hosted app to a dev stack via `platform.devStackId` for shared infra without port clashes |
| **Split API + UI routes** | Traefik rules for React/Vue frontends and `/api` backends (same pattern as production edge configs) |

### AI & automation

| Capability | What it does for you |
|------------|----------------------|
| **Ollama** | Pull and run local models; manage from the dashboard |
| **Open WebUI** | Chat UI at `https://ai.lh` wired to your local models |
| **AirLLM** | Ollama-compatible API for very large models on modest VRAM (layer streaming) |
| **n8n** | Workflow automation on `https://n8n.lh` beside the rest of the stack |
| **AI-assisted onboarding** | Dashboard flows to help scaffold and configure apps with provider abstraction |

### Cloud-shaped development (optional)

| Capability | What it does for you |
|------------|----------------------|
| **Cloudflare-local** | R2-, KV-, D1-, and Workers-style adapters on `*.lh` without hitting production Cloudflare |
| **Cloud VM profiles** | Install selective services on a Linux VM with real domains, TLS (mkcert / ACME / static), and external LLM APIs |
| **Update catalog** | Track ecosystem, stack, and upstream image/release updates from the dashboard |

---

## Use cases

<table>
<tr>
<td width="50%" valign="top">

### Onboard an existing app in one click

Point LEco at a repo that already has Compose or Wrangler. It **reads** the layout, **writes** manifests, **registers** the app, **merges** Traefik, and **deploys** — you skip hand-copying ports, env files, and route YAML.

**Ideal for:** teams onboarding legacy or vendor apps, consultants standing up client demos fast, anyone tired of “works on my machine” port docs.

</td>
<td width="50%" valign="top">

### Spin up a full stack from Platform

Use the **dev stack builder** for Magento, WordPress, Laravel, or infra-only bundles. **Start** gives you URLs and credentials; **Repair** fixes routing without wiping data.

**Ideal for:** e-commerce, CMS, and framework specialists who need a clean stack per customer or branch.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### Develop like production — locally

Use real hostnames and HTTPS while you code. Frontend, API, and workers each get Traefik routes; no more “works on `localhost:3000` only.”

**Ideal for:** full-stack engineers, platform engineers validating routing before deploy.

</td>
<td width="50%" valign="top">

### Many apps, one machine

Materialize multiple LEco-hosted apps from separate Git repos. Each slot has its own manifest, compose merge, and Traefik fragment — without folding every app into one mega-compose file.

**Ideal for:** agencies, consultants, and polyglot teams juggling client projects.

</td>
</tr>
<tr>
<td valign="top">

### Isolated stacks for CMS & commerce

Spin up **WordPress**, **Magento**, **Laravel**, or custom component bundles as separate dev stacks. Each stack owns its database and internal network; bind a hosted app when you need shared connections.

**Ideal for:** e-commerce devs, QA reproducing customer stacks, trainers running demos side by side.

</td>
<td valign="top">

### AI product development offline

Run **Ollama**, **Open WebUI**, and **AirLLM** on the same `lh-network` as your app. Prototype RAG, agents, and automation (n8n) without cloud API keys for every iteration.

**Ideal for:** AI engineers, hackathons, air-gapped or cost-sensitive experimentation.

</td>
</tr>
<tr>
<td valign="top">

### Cloudflare Workers & bindings — locally

Start **cloudflare-local** to exercise R2, KV, D1, and Workers-style endpoints on `*.lh`. Provision bindings from `leco-devops` and test Workers-shaped apps before CI deploys.

**Ideal for:** edge developers using Wrangler who want fast feedback loops.

</td>
<td valign="top">

### Preproduction on a cloud VM

Use **Platform** profiles and `cloud-install.sh` to stand up a selective subset of services on a VM with your domain, TLS mode, and optional external LLM providers — closer to staging than a laptop-only compose file.

**Ideal for:** small teams without a full Kubernetes estate, demos, and partner sandboxes.

</td>
</tr>
</table>

---

## How it fits together

```mermaid
flowchart LR
  subgraph You["Your machine or VM"]
    Browser["Browser"]
    Traefik["Traefik · *.lh"]
    Dash["LEco DevOps"]
    Plat["Platform · dev stacks"]
    Apps["Hosted apps"]
    AI["Ollama · WebUI · AirLLM"]
    CF["Cloudflare-local optional"]
  end
  Repo["Your app repos"]
  Browser --> Traefik
  Traefik --> Dash
  Traefik --> Plat
  Traefik --> Apps
  Traefik --> AI
  Traefik --> CF
  Repo --> Dash
  Dash -->|"read · convert · deploy"| Apps
  Dash --> Plat
```

| Layer | Role |
|-------|------|
| **DNS** (`*.lh`) | Resolve friendly hostnames to `127.0.0.1` |
| **Traefik** | TLS termination and HTTP routing |
| **ecosystem-stack** | Start order, service scripts, repair, updates |
| **LEco DevOps** | Dashboard + APIs + docs + onboarding |
| **Platform** | Cloud/local settings, bundles, isolated dev stacks |
| **`leco-devops`** | CLI — detect, onboard, platform, dev-stack |

Deep dive: [Architecture](docs/ARCHITECTURE.md) · [LECo user manual](docs/LECO_USER_MANUAL.md) · [Platform tab](docs/help/03-platform-tab.md) · [Hosted apps runbook](docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md)

---

## What you get

| | |
|---|---|
| **Application** | **LEco DevOps** — web UI + `leco-devops` CLI |
| **License** | [MIT](LICENSE) — commercial use, fork, contribute |
| **Official repository** | [github.com/leco-devops/local-ecosystem](https://github.com/leco-devops/local-ecosystem) |
| **Governance** | Community-owned; [Open source](docs/OPEN_SOURCE.md) |
| **Contact** | [leco@techtonic.systems](mailto:leco@techtonic.systems) |

---

## Start in minutes

**Prerequisites:** Docker, `*.lh` DNS, mkcert ([setup guide](docs/SETUP.md)).

```bash
git clone https://github.com/leco-devops/local-ecosystem.git
cd local-ecosystem
./ecosystem-stack/install-foundation.sh
./ecosystem-stack/ecosystem-stack.sh start
```

Open **http://localhost.lh** or **http://dashboard.lh** for the LEco DevOps dashboard.

<p align="center">
  <a href="docs/SETUP.md"><strong>→ Full first-time setup</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/DEPLOY_CLI.md"><strong>→ CLI reference</strong></a>
</p>

---

## Documentation

| Guide | For |
|-------|-----|
| [Setup](docs/SETUP.md) | First machine install (DNS, TLS, troubleshooting) |
| [Deployment](docs/DEPLOYMENT.md) | Day-2 operations and updates |
| [Platform tab](docs/help/03-platform-tab.md) | Dev stacks and cloud platform UI |
| [Architecture](docs/ARCHITECTURE.md) | System design and module map |
| [Development playbook](docs/DEVELOPMENT_PLAYBOOK.md) | Extend services and APIs |
| [PROJECT.md](docs/PROJECT.md) | Full repository technical guide |

---

## Contribute

This project is **community-driven**: fork, fix, document, and open pull requests. We welcome dev-stack presets, hosted-app samples, docs, and reviews.

<p align="center">
  <a href="CONTRIBUTING.md"><strong>Contribution guide</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/leco-devops/local-ecosystem/issues"><strong>Issues</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/leco-devops/local-ecosystem/fork"><strong>Fork</strong></a>
</p>

| | |
|---|---|
| [Contributing](CONTRIBUTING.md) | Branch workflow, changelog, safety |
| [Security](SECURITY.md) | Responsible disclosure |
| [Changelog](CHANGELOG.md) | Release history |

---

## Top contributors

| Role | Name | Links |
|------|------|--------|
| **Manager & moderator** | [Techtonic Systems Media And Research LLC](https://techtonic.systems/) | [Website](https://techtonic.systems/) · [leco@techtonic.systems](mailto:leco@techtonic.systems) |
| **Contributor** | Rajneesh Maurya | [GitHub](https://github.com/rmaurya) · [LinkedIn](https://www.linkedin.com/in/rajneeshmaurya/) |

The **official repository** is hosted under [`leco-devops`](https://github.com/leco-devops) on GitHub. **Commits and pushes** are made by contributors under their own accounts — primarily [@rmaurya](https://github.com/rmaurya) (Rajneesh Maurya).

---

## Governance

**LEco DevOps Open Project** grows through contributors and maintainers in this repository. **[Techtonic Systems Media And Research LLC](https://techtonic.systems/)** provides operational stewardship (releases, contact: [leco@techtonic.systems](mailto:leco@techtonic.systems)) — supporting the community, not replacing MIT-licensed ownership of the codebase.

Copyright (c) contributors · [NOTICE](NOTICE.md) · [OPEN_SOURCE.md](docs/OPEN_SOURCE.md)
