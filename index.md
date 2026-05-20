---
layout: default
title: LEco DevOps Open Project
---

<div class="leco-hero-panel">

<p class="leco-hero-logo">
  <img src="{{ '/assets/img/leco-logo.svg' | relative_url }}" width="320" height="64" alt="LEco DevOps" decoding="async" />
</p>

<p class="leco-tagline">
  <strong>One-click deploy for almost any application — no manual rewiring.</strong><br />
  LEco reads your repo, converts config, orchestrates Compose &amp; Traefik, and ships on <code>*.lh</code>.
</p>

<nav class="leco-nav" aria-label="On this page">
  <a href="#start-in-minutes">Get started</a>
  <a href="#platform">Platform</a>
  <a href="#features">Features</a>
  <a href="#use-cases">Use cases</a>
  <a href="#faq">FAQ</a>
  <a href="https://github.com/leco-devops/local-ecosystem/blob/main/docs/PROJECT.md">Technical guide</a>
  <a href="https://github.com/leco-devops/local-ecosystem">Source</a>
</nav>

<p class="leco-badges">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" />
  <img src="https://img.shields.io/badge/stack-Docker-2496ED?logo=docker&amp;logoColor=white" alt="Docker" />
  <img src="https://img.shields.io/badge/routing-Traefik-24A1C1" alt="Traefik" />
  <img src="https://img.shields.io/badge/community-open%20source-brightgreen" alt="Open source" />
</p>

</div>

<p class="leco-lead">
  <strong>LEco DevOps</strong> is a free, MIT-licensed local cloud edge: point it at an app repo, and it does the heavy lifting — detect existing Docker Compose, Wrangler, and env layout · generate LEco manifests · merge Traefik routes · deploy.
</p>

<section class="leco-vision-mission" aria-labelledby="vision-mission-heading">
  <h2 id="vision-mission-heading" class="leco-sr-only">Vision and mission</h2>

  <div class="leco-vision-mission__card leco-vision-mission__card--vision">
    <span class="leco-vision-mission__label">Vision</span>
    <h2>Every team deserves a truthful local cloud</h2>
    <p>
      We see a future where <strong>any developer, anywhere</strong>, runs software the way production does — real hostnames, trusted TLS, shared AI and automation — without months of bespoke wiring or vendor lock-in.
      <strong>LEco DevOps Open Project</strong> exists so the distance from <em>“I cloned the repo”</em> to <em>“it works like staging”</em> is measured in <strong>minutes</strong>, shaped by an open community that owns the roadmap and the code.
    </p>
  </div>

  <div class="leco-vision-mission__card leco-vision-mission__card--mission">
    <span class="leco-vision-mission__label">Mission</span>
    <h2>Deploy first. Configure never — unless you want to.</h2>
    <p>
      Our mission is to <strong>orchestrate</strong> the full path: read what you already built, convert it into LEco manifests, wire Traefik and Compose, and ship — one click when possible, full control when you need it.
      We build <strong>Platform</strong>, isolated dev stacks, and a control panel that feel like one product — free under MIT, governed by contributors, stewarded for the long run so open source stays the default way teams learn, ship, and collaborate.
    </p>
  </div>
</section>

## What makes LEco different

> **Our USP: deploy first, configure later.**  
> You should not have to rewrite every app for a new platform. LEco *reads* what you already have, *converts* it into LEco manifests and hosting slots, *orchestrates* the stack, and *deploys* — often in one click from the dashboard or `leco-devops onboard`.

{: .leco-usp-table}
| Step | What LEco does |
|------|----------------|
| **Read** | Scans `docker-compose.yml`, `wrangler.toml`, `.env`, Dockerfiles, and project layout from your Git tree |
| **Convert** | Builds `leco.app.yaml` + `leco.yaml`, materializes `hosting/app-available/{slug}/`, symlinks config without touching upstream |
| **Orchestrate** | Wires Compose projects, `lh-network`, Traefik host rules, optional Cloudflare-local bindings |
| **Deploy** | `compose up`, registry entry, route merge — reachable at `https://{your-app}.lh` with no hand-edited port maps |

Works for **Docker Compose apps**, **React/Vue + API splits**, **Workers + Wrangler**, and **preset CMS/framework stacks** — the same operator experience whether the source is your monorepo or a third-party checkout.

## Platform

<div class="leco-platform-callout">
  <p><strong>Platform</strong> is a unique LEco capability — not just settings. Run <strong>local or cloud VM</strong> deployments, <strong>curated service bundles</strong>, and <strong>isolated dev stacks</strong> from one dashboard tab (mirrored by <code>leco-devops platform</code> and <code>leco-devops dev-stack</code> on the CLI).</p>
</div>

| Capability | What it does for you |
|------------|----------------------|
| **Platform settings** | `config/leco-platform.yaml` — deployment mode, `base_domain`, TLS (mkcert / ACME / static / Cloudflare), which ecosystem services are enabled |
| **Ecosystem bundles** | Start or stop groups of stack services (Traefik, Postgres, AI, Cloudflare-local, …) without memorizing script order |
| **Dev stack builder** | One-click **WordPress**, **Magento**, **Laravel**, LAMP/MEAN, or custom component picks — each stack is its own Compose project and network |
| **Stack lifecycle** | **Start**, **Stop**, **Repair**, **Reinstall**, **Destroy** with live logs — fix routing in place or wipe and regenerate from template |
| **Cloud VM path** | Selective install on a Linux VM with real domains and external LLM providers — same Platform model as your laptop |
| **Bind hosted apps** | Point `platform.devStackId` at a stack so a LEco-hosted app shares DB/redis endpoints without port conflicts |

Presets and versions live in **`ecosystem-stack/config/dev-stack-presets.yaml`** and **`component-catalog.yaml`**. Generated stacks land under **`platform/dev-stacks/{id}/`** with Traefik routes in **`hosting/traefik/20-dev-stacks.yml`**.

<p class="leco-cta-row">
  <a href="https://github.com/leco-devops/local-ecosystem/blob/main/docs/help/03-platform-tab.md">Platform tab guide</a>
  <a href="https://github.com/leco-devops/local-ecosystem/blob/main/docs/DEV_STACK_ISOLATION.md" class="leco-cta--ghost">Dev stack isolation</a>
  <a href="https://github.com/leco-devops/local-ecosystem/blob/main/docs/CLOUD_VM_DEPLOYMENT.md" class="leco-cta--ghost">Cloud VM deployment</a>
</p>

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
| **Hosted app slots** | Keep upstream repos clean; overrides and symlinks live in `hosting/app-available/{slug}/` |
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

## Use cases

<div class="use-case-grid">

<div class="use-case-card">
  <h3>Onboard an existing app in one click</h3>
  <p>Point LEco at a repo that already has Compose or Wrangler. It <strong>reads</strong> the layout, <strong>writes</strong> manifests, <strong>registers</strong> the app, <strong>merges</strong> Traefik, and <strong>deploys</strong> — you skip hand-copying ports, env files, and route YAML.</p>
  <p class="use-case-card__ideal"><strong>Ideal for:</strong> teams onboarding legacy or vendor apps, consultants standing up client demos fast.</p>
</div>

<div class="use-case-card">
  <h3>Spin up a full stack from Platform</h3>
  <p>Use the <strong>dev stack builder</strong> for Magento, WordPress, Laravel, or infra-only bundles. <strong>Start</strong> gives you URLs and credentials; <strong>Repair</strong> fixes routing without wiping data.</p>
  <p class="use-case-card__ideal"><strong>Ideal for:</strong> e-commerce, CMS, and framework specialists who need a clean stack per customer or branch.</p>
</div>

<div class="use-case-card">
  <h3>Develop like production — locally</h3>
  <p>Use real hostnames and HTTPS while you code. Frontend, API, and workers each get Traefik routes; no more “works on <code>localhost:3000</code> only.”</p>
  <p class="use-case-card__ideal"><strong>Ideal for:</strong> full-stack and platform engineers validating routing before deploy.</p>
</div>

<div class="use-case-card">
  <h3>Many apps, one machine</h3>
  <p>Materialize multiple LEco-hosted apps from separate Git repos. Each slot has its own manifest, compose merge, and Traefik fragment — without one mega-compose file.</p>
  <p class="use-case-card__ideal"><strong>Ideal for:</strong> agencies, consultants, and polyglot teams juggling client projects.</p>
</div>

<div class="use-case-card">
  <h3>AI product development offline</h3>
  <p>Run <strong>Ollama</strong>, <strong>Open WebUI</strong>, and <strong>AirLLM</strong> on the same <code>lh-network</code> as your app. Prototype RAG, agents, and automation (n8n) without cloud API keys for every iteration.</p>
  <p class="use-case-card__ideal"><strong>Ideal for:</strong> AI engineers, hackathons, air-gapped or cost-sensitive experimentation.</p>
</div>

<div class="use-case-card">
  <h3>Cloudflare Workers &amp; bindings — locally</h3>
  <p>Start <strong>cloudflare-local</strong> to exercise R2, KV, D1, and Workers-style endpoints on <code>*.lh</code>. Provision bindings from <code>leco-devops</code> before CI deploys.</p>
  <p class="use-case-card__ideal"><strong>Ideal for:</strong> edge developers using Wrangler who want fast feedback loops.</p>
</div>

<div class="use-case-card">
  <h3>Preproduction on a cloud VM</h3>
  <p>Use <strong>Platform</strong> profiles and <code>cloud-install.sh</code> on a VM with your domain, TLS mode, and optional external LLM providers — closer to staging than laptop-only compose.</p>
  <p class="use-case-card__ideal"><strong>Ideal for:</strong> small teams without Kubernetes, demos, and partner sandboxes.</p>
</div>

</div>

## How it fits together

| Layer | Role |
|-------|------|
| **DNS** (`*.lh`) | Resolve friendly hostnames to `127.0.0.1` |
| **Traefik** | TLS termination and HTTP routing |
| **ecosystem-stack** | Start order, service scripts, repair, updates |
| **LEco DevOps** | Dashboard + APIs + docs + onboarding |
| **Platform** | Cloud/local settings, bundles, isolated dev stacks |
| **`leco-devops`** | CLI — detect, onboard, platform, dev-stack |

Deep dive: [Architecture](https://github.com/leco-devops/local-ecosystem/blob/main/docs/ARCHITECTURE.md) · [LECo user manual](https://github.com/leco-devops/local-ecosystem/blob/main/docs/LECO_USER_MANUAL.md) · [Platform tab](https://github.com/leco-devops/local-ecosystem/blob/main/docs/help/03-platform-tab.md)

## What you get

| | |
|---|---|
| **Application** | **LEco DevOps** — web UI + `leco-devops` CLI |
| **License** | [MIT](https://github.com/leco-devops/local-ecosystem/blob/main/LICENSE) — commercial use, fork, contribute |
| **Official repository** | [github.com/leco-devops/local-ecosystem](https://github.com/leco-devops/local-ecosystem) |
| **Governance** | Community-owned; [Open source](https://github.com/leco-devops/local-ecosystem/blob/main/docs/OPEN_SOURCE.md) |
| **Contact** | [leco@techtonic.systems](mailto:leco@techtonic.systems) |

## Start in minutes

**Prerequisites:** Docker, `*.lh` DNS, mkcert ([setup guide](https://github.com/leco-devops/local-ecosystem/blob/main/docs/SETUP.md)).

```bash
git clone https://github.com/leco-devops/local-ecosystem.git
cd local-ecosystem
./ecosystem-stack/install-foundation.sh
./ecosystem-stack/ecosystem-stack.sh start
```

Open **http://localhost.lh** or **http://dashboard.lh** for the LEco DevOps dashboard.

<p class="leco-cta-row">
  <a href="https://github.com/leco-devops/local-ecosystem/blob/main/docs/SETUP.md">Full first-time setup</a>
  <a href="https://github.com/leco-devops/local-ecosystem/blob/main/docs/DEPLOY_CLI.md" class="leco-cta--ghost">CLI reference</a>
  <a href="https://github.com/leco-devops/local-ecosystem" class="leco-cta--ghost">View on GitHub</a>
</p>
