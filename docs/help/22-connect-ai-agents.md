# Connect your AI agent

Point Claude Code, Claude Desktop, Codex, Antigravity, Cursor or any MCP client at this stack.

The dashboard generates the exact commands for **your** machine — open **MCP → 2 · Install on an
agent → Set up a specific agent** and pick your agent. This page explains the choices behind them.
The full reference, including a troubleshooting table, is
[Connect an AI agent](/?tab=docsTab&doc=connect-ai-agents).

## First decide the transport, not the client

Nearly every setup question is really this one.

| | **HTTP** — start here | **stdio** |
|---|---|---|
| Who runs the server | The `leco-mcp` container, already running | Your agent starts it |
| Install needed | **None** | A Python install (see below) |
| Another machine on the LAN | Yes | No |
| Endpoint / command | `http://localhost:8099/mcp` | `leco-mcp stdio` |

Use `http://localhost:8099/mcp` for agents on this machine — it skips Traefik, so there is less to
go wrong. Use `https://mcp.lh/mcp` from another machine on the LAN; that machine needs `*.lh` DNS
and the LEco root certificate.

> **Neither endpoint asks for a password.** Anything that can reach the port gets every read tool.
> Fine on a laptop, not fine on a public host — see
> [Production hardening](/?tab=docsTab&doc=production-hardening).

## Before you configure anything

The stack has to be up, or every client will fail in a way that looks like a client problem:

```
./ecosystem-stack/ecosystem-stack.sh start dashboard
./ecosystem-stack/ecosystem-stack.sh start mcp
```

**MCP → 1 · Server status** should show `RUNNING`, `REACHABLE`, and the tool count.

## The three mistakes that cost the most time

**1 · A GUI app cannot find `leco-mcp`.** Claude Desktop, Antigravity and JetBrains do not inherit
your shell `PATH`. A bare `leco-mcp` works when you test it in a terminal and then fails *silently*
inside the app. Always paste the **absolute** path — the dashboard fills in this machine's real one
for you.

**2 · The tool count is stale.** If your agent lists 60 tools rather than the number on the
dashboard, it is talking to an older container image:

```
./ecosystem-stack/services/mcp.sh restart
```

**3 · The config file was replaced instead of merged.** `claude_desktop_config.json` holds your
other servers too. Merge into the existing `mcpServers` object.

## Installing for stdio

Only needed for stdio; HTTP needs none of it.

```
python3 -m venv tools/mcp-server/.venv
tools/mcp-server/.venv/bin/pip install -e tools/mcp-server
tools/mcp-server/.venv/bin/leco-mcp doctor
```

Use the absolute path to `tools/mcp-server/.venv/bin/leco-mcp` in your client config. `pipx install
./tools/mcp-server` is the alternative if you want it on your `PATH`.

> `pip install --user` will not work on a current macOS or Linux Python — it refuses with
> `externally-managed-environment`. Use the venv above or pipx.

## What an agent may do

Read tools are always available. Destructive ones need **both** a server flag and `confirm=true` on
the call, and are off by default — see **MCP → 1 · Server status → Safety gates**. Every call is
recorded in **6 · Activity log**, so you can answer "which agent touched which application, and
when" afterwards.

## Check it worked

1. **MCP → 4 · Connected agents** — your client appears once it has called something.
2. Ask the agent to run `leco_server_info`. It should name the dashboard URL and both gates.
3. Still stuck? `docker exec leco-mcp leco-mcp doctor` separates *dashboard down* from *token
   missing* from *gate closed*.

## Related

- [Connect an AI agent](/?tab=docsTab&doc=connect-ai-agents) — every client, with a troubleshooting table
- [MCP server](help:mcp-server) — what the tools do
- [MCP server guide](/?tab=docsTab&doc=mcp-server) — full reference
