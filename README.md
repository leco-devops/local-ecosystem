<p align="center">
  <a href="https://techtonic.systems/" title="Techtonic Systems Media and Research LLC">
    <strong>Techtonic Systems</strong>
  </a>
  <br />
  <sub>Open-source software · Media and Research</sub>
</p>

<h1 align="center">LEco DevOps Open Project</h1>

<p align="center">
  <strong>Your local cloud edge on Docker</strong><br />
  Traefik · TLS · AI · automation · app lifecycle — all on <code>*.lh</code>
</p>

<p align="center">
  <a href="docs/SETUP.md"><strong>Get started</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/PROJECT.md"><strong>Repository guide</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/rmaurya/local-ecosystem"><strong>View source</strong></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" />
  <img src="https://img.shields.io/badge/open%20source-yes-brightgreen.svg" alt="Open source" />
  <img src="https://img.shields.io/badge/maintained%20by-Techtonic%20Systems-8b5cf6.svg" alt="Maintained by Techtonic Systems" />
</p>

---

## What you get

**LEco DevOps Open Project** is a **free, open-source** platform for developers who want a realistic local stack: named HTTPS hosts, a control dashboard, LLM tooling, and repeatable app deploys — without wiring ports by hand.

| | |
|---|---|
| **Application** | **LEco DevOps** — web UI + `leco-devops` CLI |
| **License** | [MIT](LICENSE) — use commercially, fork, contribute |
| **Maintainer** | [Techtonic Systems Media and Research LLC](https://techtonic.systems/) |

---

## Why teams use it

- **Edge routing** — Traefik on `https://n8n.lh`, `https://ai.lh`, `https://dashboard.lh`, and more  
- **AI ready** — Ollama, Open WebUI, AirLLM (Ollama-compatible API)  
- **Operations UI** — metrics, logs, Control tab, hosted apps, Platform / dev stacks  
- **App toolchain** — LEco manifests, Traefik routes, isolated dev-stack compose projects  
- **Optional Cloudflare-local** — R2, KV, D1, Workers-style adapters on `*.lh`  

---

## Start in minutes

Prerequisites: **Docker**, **`*.lh` DNS**, **mkcert** (see the setup guide).

```bash
git clone https://github.com/rmaurya/local-ecosystem.git
cd local-ecosystem
./ecosystem-stack/install-foundation.sh
./ecosystem-stack/ecosystem-stack.sh start
```

Open **http://localhost.lh** or **http://dashboard.lh** for the LEco DevOps dashboard.

<p align="center">
  <a href="docs/SETUP.md">→ Full first-time setup (DNS, TLS, troubleshooting)</a>
</p>

---

## Documentation

| | |
|---|---|
| [Setup](docs/SETUP.md) | First machine install |
| [Deployment](docs/DEPLOYMENT.md) | Day-2 operations |
| [Architecture](docs/ARCHITECTURE.md) | How it fits together |
| [Develop](docs/DEVELOPMENT_PLAYBOOK.md) | Extend services and APIs |
| [Open source](docs/OPEN_SOURCE.md) | License, stewardship, contributing |

**Full technical guide:** [docs/PROJECT.md](docs/PROJECT.md)

---

## Contribute and report issues

This project is **open source**. We welcome issues and pull requests.

| | |
|---|---|
| [Contributing](CONTRIBUTING.md) | Branch workflow, changelog, safety |
| [Security](SECURITY.md) | Responsible disclosure |
| [Changelog](CHANGELOG.md) | Release history |

---

## GitHub Pages

To publish this landing page: **Repository → Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main` → Folder: `/ (root)`**. GitHub will serve this `README.md` as your site home.

---

## Stewardship

**LEco DevOps Open Project** is an **open-source project** managed by **[Techtonic Systems Media and Research LLC](https://techtonic.systems/)**.

Copyright (c) Techtonic Systems Media and Research LLC and contributors. Licensed under the [MIT License](LICENSE). See [NOTICE](NOTICE.md).

<p align="center">
  <a href="https://techtonic.systems/"><strong>techtonic.systems</strong></a>
</p>
