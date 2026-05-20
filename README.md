<p align="center">
  <a href="https://techtonic.systems/" title="Techtonic Systems Media And Research LLC">
    <strong>Techtonic Systems Media And Research LLC</strong>
  </a>
  <br />
  <sub>Open-source software · <a href="https://techtonic.systems/">techtonic.systems</a></sub>
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
  <img src="https://img.shields.io/badge/steward-Techtonic%20Systems%20Media%20And%20Research%20LLC-8b5cf6.svg" alt="Steward: Techtonic Systems Media And Research LLC" />
</p>

---

## Contribute to this project

**LEco DevOps Open Project** is community-driven open source. Whether you fix a bug, improve docs, add a dev-stack preset, or review a pull request — your help matters.

<p align="center">
  <a href="CONTRIBUTING.md"><strong>Contribution guide</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/rmaurya/local-ecosystem/issues"><strong>Open an issue</strong></a>
  &nbsp;·&nbsp;
  <a href="https://github.com/rmaurya/local-ecosystem/fork"><strong>Fork &amp; pull request</strong></a>
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
| **Steward** | [Techtonic Systems Media And Research LLC](https://techtonic.systems/) |
| **Contact** | [leco@techtonic.systems](mailto:leco@techtonic.systems) |

---

## Top contributors

| Contributor | Links |
|-------------|--------|
| **Rajneesh Maurya** | [GitHub](https://github.com/rmaurya) · [LinkedIn](https://www.linkedin.com/in/rajneeshmaurya/) |

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

## Stewardship

**LEco DevOps Open Project** is an **open-source project** managed by **[Techtonic Systems Media And Research LLC](https://techtonic.systems/)**.

**Contact:** [leco@techtonic.systems](mailto:leco@techtonic.systems)

Copyright (c) Techtonic Systems Media And Research LLC and contributors. Licensed under the [MIT License](LICENSE). See [NOTICE](NOTICE.md).

<p align="center">
  <a href="https://techtonic.systems/"><strong>Techtonic Systems Media And Research LLC</strong></a>
  <br />
  <a href="mailto:leco@techtonic.systems">leco@techtonic.systems</a>
</p>
