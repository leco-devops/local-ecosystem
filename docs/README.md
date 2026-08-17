# Documentation index

*Where to look for things. Last reviewed: 2026-08-17.*

This page is navigational: it answers **"where do I look for X"**, not "what is X". 148 markdown documents live in this repository; this is the way in.

New to the project entirely? Start with **[`../START_HERE.md`](../START_HERE.md)** — the route map from install to first deploy. This page is the reference behind it.

---

## By what you are trying to do

| I want to… | Read |
|---|---|
| Install LEco on this machine | [SETUP.md](SETUP.md) |
| Understand what the thing *is* | [PROJECT.md](PROJECT.md), then [ARCHITECTURE.md](ARCHITECTURE.md) |
| Put an application on a hostname | [LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md) |
| Onboard from a Git URL, or deploy on push | [GIT_AND_CICD.md](GIT_AND_CICD.md) |
| Let an AI agent drive the platform | [MCP_SERVER.md](MCP_SERVER.md) |
| Run this on a real domain | **[PRODUCTION_HARDENING.md](PRODUCTION_HARDENING.md) first**, then [CLOUD_VM_DEPLOYMENT.md](CLOUD_VM_DEPLOYMENT.md) |
| Fix a 502, a 404, or a route | [HOSTED_APPS_TRAEFIK_RUNBOOK.md](HOSTED_APPS_TRAEFIK_RUNBOOK.md) |
| Spin up WordPress / Magento / Laravel | [DEV_STACK_ISOLATION.md](DEV_STACK_ISOLATION.md) |
| Use the CLI | [DEPLOY_CLI.md](DEPLOY_CLI.md) |
| Change the code | [DEVELOPMENT_PLAYBOOK.md](DEVELOPMENT_PLAYBOOK.md), then [LLD.md](LLD.md) |
| Contribute | [../CONTRIBUTING.md](../CONTRIBUTING.md) and [../AGENTS.md](../AGENTS.md) |

---

## What is authoritative, and what is not

This distinction is invisible from a filename and expensive to learn the hard way.

**Authoritative — these are maintained and describe current behaviour:**

- [ARCHITECTURE.md](ARCHITECTURE.md) · [HLD.md](HLD.md) · [LLD.md](LLD.md) — design, current as of 2026-08-17
- [MCP_SERVER.md](MCP_SERVER.md) · [GIT_AND_CICD.md](GIT_AND_CICD.md) · [PRODUCTION_HARDENING.md](PRODUCTION_HARDENING.md)
- [LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md) · [DEPLOY_CLI.md](DEPLOY_CLI.md) · [SETUP.md](SETUP.md) · [DEPLOYMENT.md](DEPLOYMENT.md)
- Everything under [`help/`](help/) — served live in the dashboard's **Help** tab, so drift is visible to users

**Historical — accurate for their moment, not a status report:**

- [`../releases/`](../releases/) and [`../CHANGELOG.md`](../CHANGELOG.md) — what shipped, when
- [RELEASE_NOTES.md](RELEASE_NOTES.md) — the release index
- [AI_ONBOARDING_PLAN.md](AI_ONBOARDING_PLAN.md) — design intent for AI-assisted onboarding; the shipped surface is on the Service hubs page
- [SRS_CLOUD_VM_PLATFORM.md](SRS_CLOUD_VM_PLATFORM.md) — requirements as written, not a description of what exists

**A document is a lead, never a status.** When a document and the code disagree, the code wins — say so and fix the document.

---

## The clusters

Eight groups, defined in [`../project-atlas.config.json`](../project-atlas.config.json). First match wins, so order is significant.

| Cluster | What is in it |
|---|---|
| **Start here** | This page, the root README, `START_HERE.md`, `CONTRIBUTING.md`, `PROJECT.md` |
| **Agent instructions** | `AGENTS.md` and the Claude Code plugin under `tools/claude-plugin/` |
| **Operator & developer manuals** | All of [`help/`](help/) — the Help tree served at `/help` |
| **Architecture & design** | `ARCHITECTURE.md`, `HLD.md`, `LLD.md`, service maps, the SRS |
| **Applications & onboarding** | Manifests, hosting slots, the CLI, Git sources, CI/CD, `hosting/samples/` |
| **Install & operations** | Setup, deployment, hardening, cloud VM, TLS |
| **Integrations & services** | MCP server, AirLLM, file transfer, Cloudflare-local, credential vault |
| **Releases & history** | Changelog, releases, versioning, licensing |

---

## Two conventions worth knowing before you edit

**Help pages use in-app links, not file paths.** Inside [`help/`](help/), `help:<id>` and `/?tab=docsTab&doc=<id>` are routes resolved at render time by `dashboard/help_manual.py` and `dashboard/docs_catalog.py`. They are not relative paths and will not resolve on disk. A new help page must be registered in `help_manual.py`, and a new canonical doc in `docs_catalog.py`, or it is unreachable from the UI.

**A canonical doc and its help page are a pair.** `MCP_SERVER.md` carries the depth; `help/20-mcp-server.md` is the task-shaped operator version. Keep them differentiated — identical titles across a pair is a real defect, and the health report flags it.

---

## Known gaps

Named rather than filled, because a gap named is worth more than a gap filled plausibly.

- **Ask LEco (RAG) has no UI.** `/api/ai/rag/*` works and is documented in the manuals; there is no screen.
- **No document covers dev-stack `reset` semantics.** Searching for it returns neighbours, not an answer.
- **`deployment_mode` is not validated.** Any value other than `cloud` behaves as local; nothing enforces the enum.
- **6 documents remain unclassified** — mostly `NOTICE.md`, brand assets, and adapter READMEs. Harmless.

---

*Health report: `atlas health`. Rebuild this knowledgebase: `atlas all`.*
