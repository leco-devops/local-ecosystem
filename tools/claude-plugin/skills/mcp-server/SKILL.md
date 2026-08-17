---
name: mcp-server
description: Check LEco DevOps MCP server status and fix it when the leco_* tools are missing, failing, or blocked — install, transports, dashboard URL, control token, and the destructive and credential safety gates. Argument (optional) is "doctor", "install", or the symptom you are seeing.
---

Report on and repair the LEco DevOps MCP server connection.

Context: **$ARGUMENTS**

## If the `leco_*` tools exist

`leco_server_info` is the whole first answer: the resolved dashboard URL, whether a control
token is configured, and whether the destructive and credential gates are open. Nearly every
"the LEco tools are broken" report is settled here.

Read the result against the symptom:

| Symptom | Meaning |
|---------|---------|
| Every tool fails with "dashboard unreachable" | The dashboard container is down. Start `ai-dashboard` — see `/leco:up`. The MCP server is fine |
| Control actions return **401** | `LECO_MCP_CONTROL_TOKEN` is unset or does not match the dashboard's `DASHBOARD_CONTROL_TOKEN`. Read tools keep working |
| `Blocked destructive action` | Working as designed. Report it — see the gates below |
| Credential tools missing entirely | `LECO_MCP_ALLOW_CREDENTIALS` is `0` |
| Responses cut off mid-answer | `LECO_MCP_MAX_RESPONSE_CHARS` (60 000). The fix is a narrower filter, not a bigger cap |

## If there are no `leco_*` tools at all

This is an install problem, not a platform failure. Say that first, then work the ladder:

```bash
pip install -e tools/mcp-server      # or: pipx install ./tools/mcp-server
leco-mcp doctor                      # resolved URL, token, gates, full tool list; non-zero exit if unreachable
```

`leco-mcp doctor` is the authoritative diagnosis — run it before concluding anything.

Registering it with Claude Code, either way:

```bash
# as this plugin (skill + /leco:* commands + MCP in one install)
claude plugin marketplace add ./
claude plugin install leco@leco-devops-open-project

# or the bare server
claude mcp add leco-devops -- leco-mcp stdio
```

`claude mcp list` should then report the `leco-devops` server **Connected**. A plugin that
installs but yields no tools almost always means `leco-mcp` itself is not importable.

## How the plugin finds the binary

`bin/leco-mcp-launch` resolves `leco-mcp` in this order, and stops at the first hit:

1. `$LECO_MCP_BIN` — an explicit path to an executable (use this for a virtualenv install);
2. `leco-mcp` on `PATH`;
3. `python -m leco_mcp` when the package is importable (override with `$LECO_MCP_PYTHON`);
4. the repository source tree — next to the plugin, or under `$LECO_ECOSYSTEM_ROOT` /
   `$CLAUDE_PROJECT_DIR`.

If none resolve it exits with the install command rather than failing silently. So "the
launcher printed an install hint" is a resolution failure, not a crash.

## Configuration

| Variable | Default | Effect |
|----------|---------|--------|
| `LECO_MCP_DASHBOARD_URL` | `http://localhost:8090` | Dashboard base URL; `http://dashboard.lh` also works |
| `LECO_MCP_CONTROL_TOKEN` | — | Must match the dashboard's `DASHBOARD_CONTROL_TOKEN` |
| `LECO_MCP_ALLOW_DESTRUCTIVE` | `0` | Required, **in addition to** `confirm=true`, for anything that deletes |
| `LECO_MCP_ALLOW_CREDENTIALS` | `0` | Required for the UI credential-vault tools |
| `LECO_MCP_READ_TIMEOUT` / `LECO_MCP_ACTION_TIMEOUT` | `120` / `900` | Seconds |
| `LECO_MCP_VERIFY_TLS` / `LECO_MCP_MAX_RESPONSE_CHARS` | `1` / `60000` | TLS verification; response cap |

**Opening a gate is the user's decision, not yours.** If work is blocked by
`LECO_MCP_ALLOW_DESTRUCTIVE=0`, tell them the variable, tell them the server must be
restarted for it to take effect, and stop. Do not set it, do not edit `.mcp.json` to set it,
and do not route around the block with a shell command — the shell has no gate at all, which
is precisely why the block exists.

## Transports

`leco-mcp stdio` is what Claude Code uses. `leco-mcp http --host 127.0.0.1 --port 8099`
serves streamable HTTP at `/mcp` for sharing one server between clients; in-stack it runs as
the `ai-mcp` container behind `mcp.lh`, and registers with
`claude mcp add --transport http leco-devops <url>`. The HTTP container has **no Docker
socket** — it can do no more than the dashboard API already allows.

## Full detail

`docs/MCP_SERVER.md` in the repository, and `leco_help(search="mcp")` for the operator and
developer manual pages.
