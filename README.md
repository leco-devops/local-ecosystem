<h1 align="center">LEco DevOps Open Project</h1>

<p align="center">
  <strong>Community-driven open source</strong><br />
  A local cloud edge on Docker — Traefik · TLS · AI · automation · app lifecycle on <code>*.lh</code>
</p>

<p align="center">
  <a href="docs/SETUP.md"><strong>Get started</strong></a>
  &nbsp;·&nbsp;
  <a href="docs/PROJECT.md"><strong>Repository guide</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/leco-devops/local-ecosystem"><strong>View source</strong></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" />
  <img src="https://img.shields.io/badge/open%20source-yes-brightgreen.svg" alt="Open source" />
  <img src="https://img.shields.io/badge/community--driven-welcome-orange.svg" alt="Community driven" />
</p>

---

## Contribute to this project

This repository belongs to the **community**: anyone can use it, fork it, improve it, and ship changes via pull request. We welcome bugs fixes, docs, dev-stack presets, and reviews.

<p align="center">
  <a href="CONTRIBUTING.md"><strong>Contribution guide</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/leco-devops/local-ecosystem/issues"><strong>Open an issue</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/leco-devops/local-ecosystem/fork"><strong>Fork &amp; pull request</strong></a>
</p>

<p align="center">
  <em>New here?</em> Read <a href="docs/DEVELOPMENT_PLAYBOOK.md">Development playbook</a> and <a href="docs/help/03-platform-tab.md">Platform tab guide</a> before your first PR.
</p>

---

## What you get

**LEco DevOps Open Project** is a **free, open-source** platform for developers who want a realistic local stack: named HTTPS hosts, a control dashboard, LLM tooling, and repeatable app deploys — without wiring ports by hand.

| | |
|---|---|
| **Application** | **LEco DevOps** — web UI + `leco-devops` CLI |
| **License** | [MIT](LICENSE) — use commercially, fork, contribute |
| **Governance** | Community-owned; see [Open source](docs/OPEN_SOURCE.md) |
| **Official repository** | [github.com/leco-devops/local-ecosystem](https://github.com/leco-devops/local-ecosystem) |
| **Contact** | [leco@techtonic.systems](mailto:leco@techtonic.systems) |

---

## Top contributors

| Role | Name | Links |
|------|------|--------|
| **Manager & moderator** | [Techtonic Systems Media And Research LLC](https://techtonic.systems/) | [Website](https://techtonic.systems/) · [leco@techtonic.systems](mailto:leco@techtonic.systems) |
| **Contributor** | Rajneesh Maurya | [GitHub](https://github.com/rmaurya) · [LinkedIn](https://www.linkedin.com/in/rajneeshmaurya/) |

The **official repository** lives under the [`leco-devops`](https://github.com/leco-devops) organization on GitHub. **Commits and pushes** are made by contributors under their own GitHub accounts — primarily [@rmaurya](https://github.com/rmaurya) (Rajneesh Maurya).

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
git clone https://github.com/leco-devops/local-ecosystem.git
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
| [Open source](docs/OPEN_SOURCE.md) | License, governance, contributing |

**Full technical guide:** [docs/PROJECT.md](docs/PROJECT.md)

---

## Security & releases

| | |
|---|---|
| [Contributing](CONTRIBUTING.md) | Branch workflow, changelog, safety |
| [Security](SECURITY.md) | Responsible disclosure |
| [Changelog](CHANGELOG.md) | Release history |

---

## GitHub Pages

To publish this landing page: **Repository → Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main` → Folder: `/ (root)`**. GitHub will serve this `README.md` as your site home.

---

## Governance & operational stewardship

**LEco DevOps Open Project** is **community-driven open source**. Direction, code, and documentation grow through contributors and maintainers in this repository — not through a single vendor product roadmap.

**[Techtonic Systems Media And Research LLC](https://techtonic.systems/)** provides **operational stewardship** today: release coordination, infrastructure guidance, and a contact point for the project ([leco@techtonic.systems](mailto:leco@techtonic.systems)). That role supports the community; it does not replace community ownership of the codebase under the [MIT License](LICENSE).

Copyright (c) contributors. See [NOTICE](NOTICE.md) and [OPEN_SOURCE.md](docs/OPEN_SOURCE.md).
