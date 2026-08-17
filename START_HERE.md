# START HERE — route map for AI agents and new operators

**You are in [LEco DevOps Open Project](https://github.com/leco-devops/local-ecosystem)** — a local cloud edge that runs applications on real hostnames (`*.lh`) with TLS, shared AI services, and one-click onboarding of application repositories.

This page is the **route map**: pick the row that matches what you were asked to do, and follow it. Everything links to the authoritative document — nothing is duplicated here.

> **Agents:** read [`AGENTS.md`](AGENTS.md) as well. It carries the guardrails (naming rules, change-together module sets, validation checklist) that this page does not repeat.

---

## 0. Where am I? — orient in 30 seconds

| Question | Answer |
|----------|--------|
| **What is this?** | A Docker-based platform: Traefik edge on `*.lh`, ecosystem services (Ollama, AirLLM, Open WebUI, n8n, Paperclip, Postgres), the **LEco DevOps** dashboard, and the `leco-devops` CLI |
| **What runs the show?** | The dashboard (`service-dashboard`, port `8090`, `https://dashboard.lh`). It owns lifecycle, registry, and routing |
| **How do I drive it?** | Three equivalent surfaces: the **dashboard UI**, the **`leco-devops` CLI**, and the **MCP server** (for AI agents) |
| **Is it running right now?** | `docker ps --filter name=service-dashboard` — or open `http://dashboard.lh` |
| **What version?** | [`VERSION`](VERSION) · [`version.json`](version.json) · `GET /api/version` |

**Vocabulary you need before reading anything else:**

| Term | Meaning |
|------|---------|
| **`*.lh`** | Local hostnames resolving to `127.0.0.1`; Traefik routes by hostname so you never use `localhost:3000` |
| **`lh-network`** | The shared Docker network. A container not attached to it is unreachable — this is the #1 cause of `502` |
| **Hosted app** | A third-party repo onboarded into LEco: registered in `config/leco-registry.yaml`, materialized under `hosting/app-available/<slug>/` |
| **Manifest** | `leco.app.yaml` (the bridge) + `leco.yaml` (the localhost profile). Together they describe how an app is built, routed, and deployed |
| **Dev stack** | An isolated Compose project (WordPress, Magento, Laravel, LAMP…) under `platform/dev-stacks/<id>/` with its own DB and network |
| **`wsp:`** | Path prefix for repos under the workspace parent, e.g. `wsp:MyApp`. Paths are restricted — you cannot onboard from anywhere on disk |

---

## 1. Install the platform

**First machine setup → [`docs/SETUP.md`](docs/SETUP.md)** (DNS for `*.lh`, Docker, mkcert TLS)

```bash
git clone https://github.com/leco-devops/local-ecosystem.git
cd local-ecosystem
./setup.sh                  # dependencies, service selection, DNS, TLS, dashboard login
./start.sh                  # start what you selected, in dependency order
```

`./setup.sh --yes` runs the whole thing without a single prompt — every question has a
default, so it works unattended in CI or on a headless box. `traefik` and `dashboard` are
essential and always installed; everything else is opt-in and can be added later.

Open **`http://dashboard.lh`**.

Day-to-day entry points:

| Command | Does |
|---|---|
| `./start.sh` · `--stop` · `--restart` · `--status` | stack lifecycle, in dependency order |
| `./ecosystem-stack/scripts/dns-setup.sh` | make `*.lh` resolve (`--check`, `--remove`) |
| `./ecosystem-stack/scripts/install-autostart.sh` | start at boot (systemd / launchd, `--system` for a headless Mac) |
| `./uninstall.sh --dry-run` | show everything an install put on this machine |
| `windows\Install-LecoWindows.ps1` | the Windows-side half of a WSL2 install |

Prerequisites are real: without `*.lh` DNS and a trusted mkcert root, routing and HTTPS will not work. `./setup.sh` handles both, and [`docs/SETUP.md`](docs/SETUP.md) explains what it did.

**Windows:** the platform runs inside **WSL2** — there is no native port. See [`windows/README.md`](windows/README.md) for the two things WSL2 cannot do for itself (wildcard DNS for the Windows browser, and trusting the CA in the Windows certificate store).

**Install profiles** ([`ecosystem-stack/config/install-profiles.yaml`](ecosystem-stack/config/install-profiles.yaml)) decide which services you get:

| Profile | You get |
|---------|---------|
| `minimal` | Traefik + dashboard |
| `platform` | + Postgres, n8n |
| `ai-full` | + Ollama, AirLLM, Open WebUI, Paperclip, update catalog |
| `agent-full` | + **MCP server** (AI agents drive the platform) |
| `infra-full` / `file-transfer-full` / `cloudflare-full` | + the matching bundle |
| `full` | everything |

---

## 2. Deploy an application

**The core capability.** Point LEco at a repo; it reads what you already built, converts it into manifests, wires Compose and Traefik, and deploys.

```
detect → generate manifest → register → deploy → verify
```

| How | Route |
|-----|-------|
| **Dashboard UI** | *Hosted apps* tab → *Register application* → Detect → Generate YAML → Register |
| **CLI** | `leco-devops onboard` — see [`docs/DEPLOY_CLI.md`](docs/DEPLOY_CLI.md) |
| **AI agent (MCP)** | `leco_browse` → `leco_detect` → `leco_onboard` — see [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md) |

**Read before deploying anything non-trivial:** [`docs/LECO_APP_BLUEPRINT.md`](docs/LECO_APP_BLUEPRINT.md) (bridge vs profile, hosting symlinks, compose extras, teardown semantics).

Deploying a **stack** rather than an app (WordPress, Magento, Laravel) is a different route: *Platform* tab → dev stack builder, or `leco-devops dev-stack`. See [`docs/DEV_STACK_ISOLATION.md`](docs/DEV_STACK_ISOLATION.md).

**When a fresh deploy does not answer,** go straight to [`docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md`](docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md) — it covers the failures that actually happen (`502` from containers off `lh-network`, global `404` from a stale Traefik core file, same-origin `/api` splits).

---

## 3. Use and operate it

| Task | Route |
|------|-------|
| Start / stop / restart a service | Dashboard *Control* tab · `./ecosystem-stack/ecosystem-stack.sh restart <service>` · MCP `leco_control` |
| Check health | Dashboard *Overview* · MCP `leco_status` |
| Read logs | Dashboard *Logs* tab · `./ecosystem-stack/ecosystem-stack.sh logs <service>` · MCP `leco_logs` |
| Find a service URL or credential | Dashboard *Service hubs* · MCP `leco_urls` |
| Fix routing / network drift | `./ecosystem-stack/ecosystem-stack.sh repair-network` · `./ecosystem-stack/services/traefik.sh heal` |
| Day-two operations | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) |
| Troubleshoot | [`docs/help/09-troubleshooting.md`](docs/help/09-troubleshooting.md) · [`docs/help/09-502-routing.md`](docs/help/09-502-routing.md) |

**Dependency order is not enforced for you.** Traefik first; `postgres` before `n8n`; `paperclip-postgres` before `paperclip`; dashboard before `mcp`.

**Destructive vocabulary** — know the difference before you type it: `stop` (container stays) · `remove` (container deleted, data kept) · `reset` (container **and volume** deleted) · `destroy` (dev stack and its data deleted) · `offboard` (unregistered from the ecosystem).

---

## 4. Drive it as an AI agent — MCP

The **MCP server** gives agents the same operations as the dashboard: deploy, onboard, infrastructure on/off, monitoring.

```bash
pipx install ./tools/mcp-server
leco-mcp doctor                              # verify connectivity first
claude mcp add leco-devops -- leco-mcp stdio
```

No pipx yet? `brew install pipx` (macOS) · `sudo apt install pipx` / `sudo dnf install pipx` /
`sudo pacman -S python-pipx` (Linux) · `py -m pip install --user pipx` (Windows) — then
`pipx ensurepath` and a new shell. Plain `pip install` is refused by Homebrew and distro Pythons
under PEP 668; per-platform detail is in [`docs/CONNECT_AI_AGENTS.md`](docs/CONNECT_AI_AGENTS.md#7-installing-leco-mcp-for-stdio).

Shared endpoint for remote agents: `./ecosystem-stack/services/mcp.sh start` → `https://mcp.lh/mcp`.

**Full reference → [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md)** · package [`tools/mcp-server/`](tools/mcp-server/)

**Wiring up a specific agent** — Claude Code, Claude Desktop and Cowork, Codex, Antigravity, Cursor, VS Code, or anything speaking MCP → [`docs/CONNECT_AI_AGENTS.md`](docs/CONNECT_AI_AGENTS.md). Start with the HTTP transport: the container already serves it, so there is nothing to install.

Four things an agent must internalise:

1. **Discover ids, never guess them.** `leco_control_targets` for services, `leco_apps` for applications, `leco_browse` for paths.
2. **Destructive actions are double-gated** — `confirm=true` *and* `LECO_MCP_ALLOW_DESTRUCTIVE=1`. A block is a decision, not an obstacle to route around.
3. **Diagnose before acting.** `leco_status` and `leco_logs` cost far less than an unnecessary redeploy.
4. **Payloads are big.** Use the compact defaults and filters; reach for `full=true` only when you know you need it.

---

## 5. Install the Skill and plugin (Claude Code)

The plugin bundles the MCP server **plus** operating knowledge — a skill, slash commands, and a read-only diagnostician subagent.

```bash
claude plugin marketplace add /absolute/path/to/local-ecosystem
claude plugin install leco@leco-devops-open-project
```

> Use the **absolute path** to this checkout rather than `./` — `marketplace add` resolves a
> relative path against your current directory, and you are usually standing in the app you are
> onboarding. The GitHub form clones the repo's **default branch**; if the plugin is not merged
> there you get "Marketplace file not found", which means *wrong branch*, not broken install.

You get the `leco-devops` skill (platform model, workflows, failure modes), `/leco:status`, `/leco:up`, `/leco:diagnose`, `/leco:onboard`, `/leco:routes`, and the MCP tools.

**Details → [`tools/claude-plugin/README.md`](tools/claude-plugin/README.md)**

Alternatively, the repo-root [`.mcp.json`](.mcp.json) registers the MCP server for project-scoped use — open a Claude Code session in this directory and approve it when prompted.

---

## 6. Extend or contribute

| Goal | Route |
|------|-------|
| Understand the system | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) → [`docs/HLD.md`](docs/HLD.md) → [`docs/LLD.md`](docs/LLD.md) |
| Add a service, route, or adapter | [`docs/DEVELOPMENT_PLAYBOOK.md`](docs/DEVELOPMENT_PLAYBOOK.md) |
| Change hosted-app behaviour | [`AGENTS.md`](AGENTS.md) §4 — several modules must move together |
| Work on the CLI | [`docs/DEPLOY_CLI.md`](docs/DEPLOY_CLI.md) · [`tools/deploy-cli/`](tools/deploy-cli/) |
| Contribute | [`CONTRIBUTING.md`](CONTRIBUTING.md) — branch, changelog bullet, test plan |

**Before you finish any change:**

```bash
python3 -m compileall -q dashboard tools/deploy-cli/leco_app tools/mcp-server/leco_mcp
python -m pytest tools/mcp-server/tests -q
```

Add a bullet under `[Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md) for anything user-visible, and register new docs in `dashboard/docs_catalog.py` so they appear in the UI.

---

## Decision table — "I was asked to…"

| Request | Start at |
|---------|----------|
| "Set this up on my machine" | [`docs/SETUP.md`](docs/SETUP.md) → §1 |
| "Deploy / onboard this repo" | §2 → [`docs/LECO_APP_BLUEPRINT.md`](docs/LECO_APP_BLUEPRINT.md) |
| "Onboard from a Git URL / deploy on push" | [`docs/GIT_AND_CICD.md`](docs/GIT_AND_CICD.md) |
| "Spin up WordPress / Magento / Laravel" | [`docs/DEV_STACK_ISOLATION.md`](docs/DEV_STACK_ISOLATION.md) |
| "Turn X on/off" / "restart the stack" | §3 → dashboard *Control* or `leco_control` |
| "It returns 502 / 404 / does not route" | [`docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md`](docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md) |
| "Why is the stack unhealthy?" | `leco_status` → `leco_urls(only_unhealthy=true)` → `leco_logs` |
| "Let agents control this" | §4 → [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md) |
| "Connect *my* agent to it" | [`docs/CONNECT_AI_AGENTS.md`](docs/CONNECT_AI_AGENTS.md) |
| "Run it on a cloud VM with a real domain" | [`docs/PRODUCTION_HARDENING.md`](docs/PRODUCTION_HARDENING.md) **first**, then [`docs/CLOUD_VM_DEPLOYMENT.md`](docs/CLOUD_VM_DEPLOYMENT.md) · [`docs/CLOUDFLARE_SSL_INSTALL.md`](docs/CLOUDFLARE_SSL_INSTALL.md) |
| "Add a feature / fix a bug here" | §6 → [`AGENTS.md`](AGENTS.md) |
| "Set up FTP/SFTP" | [`docs/FILE_TRANSFER.md`](docs/FILE_TRANSFER.md) |
| "Manage local LLMs" | Dashboard *Infrastructure* · MCP `leco_llm_models` |

---

**Machine-readable discovery:** [`llms.txt`](llms.txt) · [`llms-full.txt`](llms-full.txt) · [`ai.txt`](ai.txt) · [`AGENTS.md`](AGENTS.md)

MIT · [Techtonic Systems Media And Research LLC](https://techtonic.systems/) · [leco-project.us](https://leco-project.us)
