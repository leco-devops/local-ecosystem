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
| `dashboard_url` is `http://localhost:8090` | The routed hostname is tried first, so falling through to the published host port means **Traefik is not answering** even though the dashboard is up. The tools work; `*.lh` routing does not — see `/leco:routes` |
| Control actions return **401** | `LECO_MCP_CONTROL_TOKEN` is unset or does not match the dashboard's `DASHBOARD_CONTROL_TOKEN`. Read tools keep working |
| `Blocked destructive action` | Working as designed. Report it — see the gates below |
| Credential tools missing entirely | `LECO_MCP_ALLOW_CREDENTIALS` is `0` |
| Responses cut off mid-answer | `LECO_MCP_MAX_RESPONSE_CHARS` (60 000). The fix is a narrower filter, not a bigger cap |

## If there are no `leco_*` tools at all

This is an install problem, not a platform failure. Say that first, then work the ladder:

```bash
pipx install ./tools/mcp-server      # or: uv tool install ./tools/mcp-server
leco-mcp doctor                      # resolved URL, token, gates, full tool list; non-zero exit if unreachable
```

`leco-mcp doctor` is the authoritative diagnosis — run it before concluding anything. But it is the
same binary that is missing, so `command not found: leco-mcp` **is** the diagnosis: nothing is
installed. Run the launcher by hand to see why — it prints the reason and exits 0, which is why the
client only reports `CONNECTION_CLOSED`:

```bash
tools/claude-plugin/bin/leco-mcp-launch stdio </dev/null
```

**Do not suggest `pip install -e tools/mcp-server`.** A Homebrew or distro Python refuses it under
PEP 668 (`externally-managed-environment`), and a stale `leco_mcp.egg-info/` is evidence someone
already tried. Two working routes:

| Route | Commands |
|---|---|
| pipx — puts `leco-mcp` on `PATH`, no config edits | `pipx install ./tools/mcp-server` |
| venv — no new tooling, needs an env var | `python3 -m venv tools/mcp-server/.venv && tools/mcp-server/.venv/bin/pip install -e tools/mcp-server`, then set `LECO_MCP_BIN` to `tools/mcp-server/.venv/bin/leco-mcp` |

pipx does not ship with Python. Install it first — `brew install pipx` (macOS), `sudo apt install
pipx` / `sudo dnf install pipx` / `sudo pacman -S python-pipx` (Linux), `py -m pip install --user
pipx` (Windows; under WSL2 use the Linux form) — then `pipx ensurepath` and a **new shell**, since
`~/.local/bin` must be on `PATH`.

Which route to take is the user's call: pipx installs a package via Homebrew, and the venv route
means editing `.mcp.json` or `.claude/settings.local.json`. Ask before doing either.

Registering it with Claude Code, either way:

```bash
# as this plugin (skill + /leco:* commands + MCP in one install)
claude plugin marketplace add /absolute/path/to/local-ecosystem
claude plugin install leco@leco-devops-open-project

# or the bare server
claude mcp add leco-devops -- leco-mcp stdio
```

> Use the **absolute path** to this checkout rather than `./` — `marketplace add` resolves a
> relative path against your current directory, and you are usually standing in the app you are
> onboarding. The GitHub form clones the repo's **default branch**; if the plugin is not merged
> there you get "Marketplace file not found", which means *wrong branch*, not broken install.

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
| `LECO_MCP_DASHBOARD_URL` | auto-discovered | Pins the dashboard base URL. Left unset, candidates are tried in order: the routed hostname (`http://localhost.lh`, then the `http://dashboard.lh` alias, then their https forms), the published host port `http://localhost:8090`, then `http://service-dashboard:8090` |
| `LECO_MCP_BASE_DOMAIN` | from `config/leco-platform.yaml` | Domain the routed candidates are built from. Local installs are always `.lh`; cloud mode uses `dashboard.<base_domain>` |
| `DASHBOARD_HOST_PORT` | `8090` | Host port the dashboard is published on; the `localhost:<port>` fallback follows it |
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
