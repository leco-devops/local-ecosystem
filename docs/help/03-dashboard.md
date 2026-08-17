# Dashboard tour

Open **`https://localhost.lh`** (or `http://localhost.lh`, or `http://localhost:8090` if Traefik is not up yet).

## The navigation is grouped

The top bar is no longer a flat row of tabs. Six items sit beside **Overview**; four of them open a small dropdown:

| Nav item | Opens | What lives there |
|----------|-------|------------------|
| **Overview** | tab | Live CPU/RAM charts, URL probe summary, Cloudflare-local bars, container mix, hosted app URLs |
| **Deploy ▾** | **Hosted apps** · **CI/CD** · **Routes** | Everything that puts an application on a hostname |
| **Operate ▾** | **Control** · **Infrastructure** | Start/stop services, health, model managers, Docker inventory |
| **Insight ▾** | **Metrics** · **Logs** · **Reference** | Host metric history, per-container log tail, the `*.lh` URL encyclopedia |
| **Platform ▾** | **Platform** · **MCP** | Platform config + dev stacks; the MCP agent bridge |
| **Help** | page `/help` | This manual |
| **Service hubs** | page `/hub` | Per-service ops pages, UI credentials, **AI providers** |

Escape closes an open dropdown. The group highlights while one of its tabs is active.

Deep links still work per tab: `/?tab=hostedAppsTab`, `/?tab=cicdTab`, `/?tab=mcpTab`, `/?tab=platformTab`, `/?tab=referenceTab`, …

### Tabs that are not in the nav

**Docs** (`/?tab=docsTab`) and **Develop** (`/?tab=developTab`) still exist and still work — they are reached from the **page footer** links or from a direct URL such as `/?tab=docsTab&doc=production-hardening`. Long-form documentation now lives in **Help**.

## What each tab is for

| Tab | Heading you will see | Purpose |
|-----|----------------------|---------|
| Overview | *(cards + charts)* | Health at a glance; hosted app URLs with live probe colour |
| **Hosted apps** | Hosted apps | Registered apps from `config/leco-registry.yaml`; **Register application** wizard (local folder **or** Git URL); attached services; seed data; lifecycle |
| **CI/CD** | CI/CD | Pipelines, signed webhooks, run history, rollback — [Git onboarding & CI/CD](help:git-cicd) |
| **Routes** | Traefik routes | Routers, services, registry overlap, quick route builder, merge a YAML fragment |
| **Control** | Control | Start/stop/restart/pause ecosystem services and hosted stacks — [Control tab](help:dash-control) |
| **Infrastructure** | Infrastructure | Health, service cards, Ollama/AirLLM **Model manager**, Paperclip, AI onboarding status, Docker inventory — [Infrastructure tab](help:dash-infra) |
| **Metrics** | Deep metrics | Host CPU / RAM / temperature history |
| **Logs** | Service Logs | Per-container log tail |
| **Reference** | URL reference | Every `*.lh` URL grouped by category, with live probe badges. Filter box at the top. |
| **Platform** | Platform (cloud VM) | `config/leco-platform.yaml`, ecosystem bundles, **dev stack builder** — [Platform tab](help:dash-platform) |
| **MCP** | MCP agents | Which AI agents are connected and what they did — [MCP server](help:mcp-server) |

**Reference is not the Docs tab renamed.** Reference is the URL encyclopedia (`GET /api/reference`); Docs is the markdown reader. They have always been two different things.

## The MCP tab in one paragraph

**Platform → MCP** answers "which agent touched which application, and when". Seven sections: **Server status** (installed / running / reachable, exposed tool count, safety gates, activity-log file), **Install on an agent** (copyable commands for the Claude Code plugin, stdio, and HTTP), **Plugin commands** (read from the plugin on disk, so it cannot drift), **Connected agents (sessions)**, **Applications — what MCP is doing to them**, **Activity log** (every call, including ones a safety gate `blocked` — that is the guard working, not a failure), and **Tool usage**. Setup, safety model, and troubleshooting: **[MCP server](help:mcp-server)**.

## The CI/CD tab in one paragraph

**Deploy → CI/CD** holds one pipeline per application. Each run is `pull → build hook (optional) → deploy → verify → record`. **Verify is a real HTTP probe** of the app's public URL, so a deploy that finishes while the app returns 502 is recorded as **failed** — and the last-deployed commit is not advanced, which is what makes **Rollback** trustworthy. Full walkthrough: **[Git onboarding & CI/CD](help:git-cicd)**.

## Auto-refresh

Header **Auto refresh** (5s–60s, or **Manual**) reloads Overview/Infrastructure data. **Refresh now** pulls immediately. A thin progress bar at the very top names the request in flight.

## Control token

Mutating actions (model install/remove, Control lifecycle, Register, CI/CD, saving an AI provider) require `DASHBOARD_CONTROL_TOKEN` **when it is set** in `ecosystem-stack/services/dashboard.sh`. Enter it once on the **Control** tab; it is kept in browser `localStorage`.

On a laptop with no token set, these endpoints are **open**. That is fine locally and dangerous anywhere else — read **[PRODUCTION_HARDENING.md](/?tab=docsTab&doc=production-hardening)** before you put this on a real domain.

## Where is the Ollama / AirLLM model UI?

1. **Operate → Infrastructure**.
2. Use the sticky **jump bar** → **Ollama** or **AirLLM**.
3. Look for the purple **Model manager** card — not the service cards further up.

Next: [Platform tab](help:dash-platform) · [Infrastructure tab](help:dash-infra) · [Control](help:dash-control) · [Hosted apps](help:hosted-apps)
