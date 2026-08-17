# MCP server — let AI agents run LEco

The **MCP server** exposes LEco DevOps over the [Model Context Protocol](https://modelcontextprotocol.io), so Claude Code and other AI agents can deploy applications, onboard repositories, start and stop infrastructure, and read logs — the same operations you perform in this dashboard.

## Where to manage it

| Surface | What you do |
|---------|-------------|
| **MCP** tab | Connected agents, what they are doing, which apps they touched, and install commands |
| **Control** tab | Start/stop/restart **MCP server (AI agents)** (target `ai-mcp`) |
| **Infrastructure** tab | Live status of the `leco-mcp` container |

Help link: [Control tab](help:dash-control) · [Infrastructure tab](help:dash-infra)

## Two ways to connect

| Transport | Who it is for | Endpoint |
|-----------|---------------|----------|
| **stdio** | Claude Code on this machine | `leco-mcp stdio` (a local process, no container) |
| **streamable HTTP** | Remote agents, other machines, cloud sessions | `https://mcp.lh/mcp` · host `http://localhost:8099/mcp` |

You do not need the container for local Claude Code use.

## Install on an agent

The quickest path installs everything at once — MCP tools, a skill that teaches the platform, and slash commands:

```bash
claude plugin marketplace add /absolute/path/to/local-ecosystem
claude plugin install leco@leco-devops-open-project
```

> Use the **absolute path** to this checkout rather than `./` — `marketplace add` resolves a
> relative path against your current directory, and you are usually standing in the app you are
> onboarding. The GitHub form clones the repo's **default branch**; if the plugin is not merged
> there you get "Marketplace file not found", which means *wrong branch*, not broken install.

That gives you `/leco:status`, `/leco:up`, `/leco:diagnose`, `/leco:onboard`, and `/leco:routes`.

MCP server only:

```bash
pipx install ./tools/mcp-server          # or: uv tool install ./tools/mcp-server
leco-mcp doctor                          # always verify before wiring it up
claude mcp add leco-devops -- leco-mcp stdio
```

Shared HTTP endpoint for agents on other machines:

```bash
./ecosystem-stack/services/mcp.sh start
claude mcp add --transport http leco-devops https://mcp.lh/mcp
```

The **MCP** tab has all of these as copy-able commands.

## What an agent can do

| Family | Examples |
|--------|----------|
| **Watch** | Stack health, service logs, URL probes, metrics, Traefik routes |
| **Control** | Start/stop/restart/deploy any service, infra add-on, Cloudflare adapter, or hosted app |
| **Onboard** | Scan a repo, generate manifests, register, deploy, and verify — in one call |
| **Platform** | Ecosystem services, dev stacks (create, start, repair), platform settings |
| **AI** | Pull, unload, and inspect Ollama / AirLLM models |
| **Learn** | Read these help pages and the architecture docs |

## Safety — what an agent cannot do by accident

Anything that destroys data needs **two** independent permissions:

1. the agent's call must pass `confirm=true`, **and**
2. the server must have been started with `LECO_MCP_ALLOW_DESTRUCTIVE=1`.

Out of the box the second is **off**, so `remove`, `reset`, `destroy`, and offboard are refused no matter what an agent asks for. The refusal message explains both gates, so the agent tells you it was blocked instead of trying to work around it.

Credential tools are separately gated by `LECO_MCP_ALLOW_CREDENTIALS=1` because they return plaintext local-dev logins.

To allow destructive work deliberately:

```bash
LECO_MCP_ALLOW_DESTRUCTIVE=1 ./ecosystem-stack/services/mcp.sh restart
```

The **MCP** tab shows which gates are currently open.

## Verify it works

```bash
leco-mcp doctor                          # local install
docker exec leco-mcp leco-mcp doctor     # HTTP service
```

You should see `reachable: true`, the dashboard URL it resolved, and 65 tools. If the dashboard requires a control token and the MCP server does not have it, `doctor` warns you — that is the case where reads work but every action fails with 401.

## If something is wrong

| Symptom | Fix |
|---------|-----|
| Agent says the dashboard is unreachable | Start the stack: `./ecosystem-stack/ecosystem-stack.sh start dashboard` |
| Reads work, actions fail with 401 | Set `LECO_MCP_CONTROL_TOKEN` to the same value as `DASHBOARD_CONTROL_TOKEN` |
| Agent reports "blocked destructive action" | Working as intended — see **Safety** above |
| `https://mcp.lh` returns 404 | `./ecosystem-stack/services/traefik.sh ensure-hosting-files` |
| `https://mcp.lh` returns 502 | `./ecosystem-stack/ecosystem-stack.sh repair-network` |
| A long deploy is cut off | Raise `LECO_MCP_ACTION_TIMEOUT` (default 900s) |

More: [502 / routing](help:ts-502) · [Troubleshooting](help:ts-common)

## Full reference

The complete guide — configuration, all 65 tools, response shaping, and the design rationale — is [MCP_SERVER.md](/?tab=docsTab&doc=mcp-server) in the **Docs** tab. Developer detail: [MCP server (developer)](help:dev-mcp-server).
