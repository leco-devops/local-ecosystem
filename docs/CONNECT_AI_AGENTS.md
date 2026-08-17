# Connect an AI agent to LEco

**Last verified:** 2026-08-17 on macOS (Darwin 25.6), against `leco-mcp` 0.1.0 exposing **65 tools**.

Every command, file path and config shape below was run or read on a real machine. Where a client
could not be tested here, it is marked **untested** and says so — rather than presenting a plausible
snippet as a verified one.

---

## 0. Start here: pick a transport, not a client

Almost every "how do I connect X" question is really "which transport". There are two, and the
choice is usually made for you:

| | **HTTP** (recommended) | **stdio** |
|---|---|---|
| What runs the server | The `leco-mcp` container, already running | Your agent spawns it |
| Install needed | **None** | Python package install |
| Works from another machine | Yes, on the LAN | No |
| Works in GUI apps with no shell | Yes | Often not |
| Endpoint / command | `http://localhost:8099/mcp` | `leco-mcp stdio` |

**Start with HTTP.** The container is already serving it, so there is nothing to install and nothing
to keep in sync when LEco updates. Reach for stdio only when a client does not support HTTP MCP.

Two URLs, and the difference matters:

| URL | Use when |
|---|---|
| `http://localhost:8099/mcp` | The agent runs on **this** machine. Bypasses Traefik — fewest moving parts, so try this first when debugging |
| `https://mcp.lh/mcp` | The agent runs **elsewhere on the LAN**. Goes through Traefik with TLS; that machine needs `*.lh` DNS and the LEco root certificate |

> **There is no authentication on either endpoint.** Anyone who can reach the port gets the full
> tool surface, including read access to logs, routes and configuration. That is acceptable on a
> laptop and is **not** acceptable on a public host — see
> [PRODUCTION_HARDENING.md](PRODUCTION_HARDENING.md). Destructive tools stay double-gated
> regardless (§6).

### Prerequisite: the stack must be up

```bash
./ecosystem-stack/ecosystem-stack.sh start dashboard
./ecosystem-stack/ecosystem-stack.sh start mcp
```

Confirm before configuring any client — a client that cannot connect is far more confusing to debug
than a stack that is plainly down:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST http://localhost:8099/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}'
```

`200` means the server is answering MCP. Anything else: `docker logs leco-mcp`.

> **`Accept` must list both `application/json` and `text/event-stream`.** The streamable-HTTP
> transport rejects a request that accepts only JSON. If you are hand-rolling a client and getting
> a 406, this is why.

---

## 1. Claude Code (CLI)

Three ways in. They do the same thing; pick one.

### 1a. The plugin — recommended

Bundles the MCP server, the `operate` skill and the slash commands in one install.

```bash
claude plugin marketplace add /absolute/path/to/local-ecosystem
claude plugin install leco@leco-devops-open-project
```

**Use the absolute path, not `./`.** `marketplace add` resolves a relative path against your
shell's working directory, and the natural moment to run this is while you are standing in the
*application* you want to onboard — not in this repo. The dashboard prints this machine's real
path under **MCP → 2 · Install on an agent**.

### The GitHub form, and why it may fail

```bash
claude plugin marketplace add leco-devops/local-ecosystem
```

This clones the repository's **default branch**. If the plugin has not been merged there yet, the
clone succeeds, the marketplace file is absent, and you get:

```
✘ Failed to add marketplace: Marketplace file not found at
  ~/.claude/plugins/marketplaces/leco-devops-local-ecosystem/.claude-plugin/marketplace.json
```

That message reads like a broken install; it actually means *this branch does not carry the
plugin*. Check before blaming the tool:

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  https://raw.githubusercontent.com/leco-devops/local-ecosystem/main/.claude-plugin/marketplace.json
```

`404` → use the local-path form above. There is no `--branch` flag on `marketplace add`.

Then `/leco:status`, `/leco:onboard`, `/leco:diagnose`, `/leco:routes`, `/leco:up`, and more — the
**MCP → 3 · Plugin commands** table lists every one with its description.

> Uninstall any older `leco-devops@…` plugin first, or both appear in the command list.

### 1b. Project-scoped — the repo already ships it

[`.mcp.json`](../.mcp.json) at the repo root registers the server for anyone working in this
checkout. Nothing to run: open Claude Code here and approve the server when prompted.

It calls [`tools/claude-plugin/bin/leco-mcp-launch`](../tools/claude-plugin/bin/leco-mcp-launch),
which finds `leco-mcp` from `LECO_MCP_BIN`, then `PATH`, then an importable `leco_mcp`, then the
source tree — so it keeps working across all the install styles in §7.

### 1c. Register by hand

```bash
# HTTP — nothing to install
claude mcp add --transport http leco-devops http://localhost:8099/mcp

# stdio — needs §7 first
claude mcp add leco-devops -- leco-mcp stdio
```

Scope with `-s user` (all projects) or `-s project` (writes `.mcp.json`). Default is `local`.

**Verify:** `claude mcp list` shows `leco-devops` connected. Then ask the agent to call
`leco_server_info` — it should report the dashboard URL and both safety gates.

---

## 2. Claude Desktop — and Cowork

Both read the **same file**. Cowork is part of the desktop app, so configuring one configures the
other; `claude_desktop_config.json` on this machine already carries a `coworkUserFilesPath` beside
its `mcpServers` block.

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

**Merge** this into the existing `mcpServers` object — do not replace the file, it holds your other
servers and your preferences:

```json
{
  "mcpServers": {
    "leco-devops": {
      "command": "/absolute/path/to/local-ecosystem/tools/mcp-server/.venv/bin/leco-mcp",
      "args": ["stdio"]
    }
  }
}
```

**Use an absolute path.** The desktop app does not inherit your shell's `PATH`, so a bare
`leco-mcp` resolves in a terminal and fails silently in the app — the single most common cause of
"the server just doesn't appear". Get the path with:

```bash
echo "$(pwd)/tools/mcp-server/.venv/bin/leco-mcp"
```

Quit and reopen the app fully — a window close is not a restart. The server appears under the
tools/connector icon.

> **Prefer HTTP?** Desktop's support for remote MCP servers is delivered through **Connectors** in
> Settings rather than this file, and a connector must be reachable from Anthropic's side — a
> `localhost` or `*.lh` URL is not. For a laptop stack, stdio is the working choice here.

---

## 3. Codex CLI — verified working

```bash
# HTTP — recommended, nothing to install
codex mcp add leco-devops --url http://localhost:8099/mcp

# stdio — needs §7 first
codex mcp add leco-devops -- leco-mcp stdio
```

Check it:

```console
$ codex mcp get leco-devops
leco-devops
  enabled: true
  transport: streamable_http
  url: http://localhost:8099/mcp
```

The equivalent hand-edit of `~/.codex/config.toml` — note it is **TOML**, and the table is
`mcp_servers` with an underscore:

```toml
[mcp_servers.leco-devops]
url = "http://localhost:8099/mcp"

# stdio form instead:
# [mcp_servers.leco-devops]
# command = "/absolute/path/to/tools/mcp-server/.venv/bin/leco-mcp"
# args = ["stdio"]
```

Remove with `codex mcp remove leco-devops`.

---

## 4. Antigravity (Google)

Antigravity keeps its MCP servers in a dedicated file, which exists (empty) on a fresh install:

```
~/.gemini/antigravity/mcp_config.json
```

Standard `mcpServers` shape:

```json
{
  "mcpServers": {
    "leco-devops": {
      "serverUrl": "http://localhost:8099/mcp"
    }
  }
}
```

If that key is not accepted by your build, use the stdio form, which every MCP client supports:

```json
{
  "mcpServers": {
    "leco-devops": {
      "command": "/absolute/path/to/tools/mcp-server/.venv/bin/leco-mcp",
      "args": ["stdio"]
    }
  }
}
```

Restart Antigravity, then open the MCP/tools panel and confirm `leco-devops` lists 65 tools.

> **Untested here.** The path above was read from a real Antigravity install on this machine and
> the file was empty, so the *location* is confirmed but the accepted remote-URL key is not. The
> stdio form is the safe fallback if the first does not connect.

---

## 5. Other MCP clients

All of these take the same two shapes. **Untested here** unless noted — the shapes are standard MCP
client config, and the LEco-specific values (command, URL, env) are the verified part.

| Client | Config file | Notes |
|---|---|---|
| **Cursor** | `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per project) | Standard `mcpServers`. The file does not exist until you create it |
| **VS Code / Copilot** | `.vscode/mcp.json` in the workspace | Uses a `servers` key rather than `mcpServers` |
| **Windsurf** | `~/.codeium/windsurf/mcp_config.json` | Standard `mcpServers` |
| **Cline / Roo** | Through the extension's MCP settings pane | Standard `mcpServers` |
| **Zed** | `settings.json` → `context_servers` | Different key name |
| **Continue** | `~/.continue/config.yaml` | YAML, `mcpServers` list |
| **JetBrains AI** | Settings → Tools → AI Assistant → MCP | GUI form: command + args, or URL |
| **Gemini CLI** | `~/.gemini/settings.json` | `mcpServers` |

Generic stdio block, portable to any of them:

```json
{
  "mcpServers": {
    "leco-devops": {
      "command": "/absolute/path/to/tools/mcp-server/.venv/bin/leco-mcp",
      "args": ["stdio"]
    }
  }
}
```

Generic HTTP block:

```json
{
  "mcpServers": {
    "leco-devops": { "url": "http://localhost:8099/mcp" }
  }
}
```

> Clients disagree on the remote-URL key: `url`, `serverUrl`, `httpUrl`, or a `"type": "http"`
> field beside `url`. If a remote entry will not connect, check that key first — then fall back to
> stdio, which nothing disagrees about.

### An agent on another machine

1. Point its DNS at this host for `*.lh`, or add `mcp.lh` to its `hosts` file.
2. Install the LEco root certificate on it, or the TLS handshake fails.
3. Use `https://mcp.lh/mcp`.

Skip 1–2 by using `http://<this-host-lan-ip>:8099/mcp` — plaintext, so only on a network you trust.

> **That port is already open to your whole network.** The container is started with
> `LECO_MCP_HTTP_HOST=0.0.0.0` and published as `0.0.0.0:8099`, so no change is needed to reach it
> from another machine — and, with no authentication on the endpoint, no change is needed for
> anyone else on the network to reach it either. Verified with `docker port leco-mcp`. On an
> untrusted network, stop the container or firewall the port; see
> [PRODUCTION_HARDENING.md](PRODUCTION_HARDENING.md).
>
> (The `127.0.0.1` default for `LECO_MCP_HTTP_HOST` applies only when you run `leco-mcp http`
> yourself, outside the container.)

---

## 6. Safety gates — what an agent can and cannot do

Off by default. **Both** the flag and per-call `confirm=true` are required:

| Variable | Unlocks |
|---|---|
| `LECO_MCP_ALLOW_DESTRUCTIVE=1` | `remove`, `reset`, `destroy`, `reinstall`, offboard, route strip, model delete |
| `LECO_MCP_ALLOW_CREDENTIALS=1` | Tools returning local-dev credentials in plaintext |

Leave both off unless you are deliberately letting an agent tear things down. A blocked call names
both gates, so the agent reports the refusal instead of trying to work around it.

Set them where the transport lives — in the client's `env` block for stdio, or on the container for
HTTP (`ecosystem-stack/services/mcp.sh`), which changes it for **every** connected agent at once.

Every call is written to `ecosystem-stack/config/generated/mcp-activity.jsonl` and surfaced in
**MCP → 6 · Activity log**, so "which agent touched which application, and when" is answerable
after the fact.

---

## 7. Installing `leco-mcp` for stdio

**Only needed for stdio.** HTTP needs none of this.

Check first — it may already be there:

```bash
command -v leco-mcp && leco-mcp doctor
```

### Option A — a virtualenv in the repo (works everywhere)

```bash
python3 -m venv tools/mcp-server/.venv
tools/mcp-server/.venv/bin/pip install -e tools/mcp-server
tools/mcp-server/.venv/bin/leco-mcp doctor
```

Then use the **absolute path** to `tools/mcp-server/.venv/bin/leco-mcp` in your client config. The
venv is gitignored. This is the path verified on this machine.

### Option B — pipx, for `leco-mcp` on your `PATH`

pipx is not part of Python — install it first. Pick your platform:

**macOS**

```bash
brew install pipx && pipx ensurepath
# no Homebrew:
python3 -m pip install --user pipx && python3 -m pipx ensurepath
```

**Linux**

```bash
sudo apt install pipx          # Debian / Ubuntu 23.04+
sudo dnf install pipx          # Fedora / RHEL
sudo pacman -S python-pipx     # Arch
python3 -m pip install --user pipx   # older distros, no package available
pipx ensurepath
```

**Windows**

LEco DevOps itself runs under **WSL2** — inside the WSL shell, use the Linux commands above. You
only need native Windows pipx when the *client* is a Windows app (Claude Desktop, Cursor) speaking
**stdio**; in PowerShell:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
# or, with Scoop:  scoop install pipx
```

> On Windows, the **HTTP transport** (§0) sidesteps this entirely — the container already serves it
> and nothing needs installing on the Windows side. Prefer it.

Then, from the repo root:

```bash
pipx install ./tools/mcp-server
leco-mcp doctor
```

Open a new shell afterwards so `PATH` picks up `~/.local/bin` (pipx's app directory).

> `pip install --user` is **not** a third option on modern macOS/Linux: a Homebrew or
> distro Python refuses it under PEP 668 (`externally-managed-environment`). Use a venv or pipx.
> That refusal applies to `pip install -e tools/mcp-server` too, which is why the plain `pip`
> form is not offered above.

### Option C — point the launcher at any of the above

```bash
export LECO_MCP_BIN=/absolute/path/to/leco-mcp
```

[`leco-mcp-launch`](../tools/claude-plugin/bin/leco-mcp-launch) honours this before searching
`PATH`, an importable `leco_mcp`, and the source tree.

---

## 8. Verify, then troubleshoot

Always in this order — each step rules out everything below it:

```bash
# 1 · Is the stack up?
docker ps --filter name=leco-mcp --format '{{.Names}} {{.Status}}'

# 2 · Does the server answer MCP? (the curl in §0)

# 3 · Does the server see the dashboard, and how many tools?
docker exec leco-mcp leco-mcp doctor     # HTTP transport
tools/mcp-server/.venv/bin/leco-mcp doctor   # stdio install

# 4 · Ask the agent for leco_server_info
```

`doctor` should report `reachable: true` and **65** tools. It separates *dashboard down* from
*token missing* from *gate closed*, which covers nearly every failure.

| Symptom | Cause | Fix |
|---|---|---|
| Client shows the server but **0 tools** | Connected to a stale container | Rebuild: `./ecosystem-stack/services/mcp.sh restart` |
| Tool count is **60, not 65** | Same — image predates the current tools | As above |
| GUI app: server never appears, no error | Bare `leco-mcp` not on the app's `PATH` | Use the absolute venv path (§2) |
| `command not found: leco-mcp` | Not installed | §7 |
| `command not found: pipx` | pipx is not bundled with Python | Install it per-platform (§7, Option B) |
| `leco-mcp` installs but stays "not found" | `~/.local/bin` not on `PATH` yet | `pipx ensurepath`, then open a new shell |
| `externally-managed-environment` | PEP 668 blocking `pip install` | venv or pipx (§7) |
| Launcher prints an install hint, client shows `CONNECTION_CLOSED` | Source tree found, dependencies missing — nothing is installed | §7 |
| HTTP 406 from the endpoint | `Accept` missing `text/event-stream` | Send both content types (§0) |
| Remote entry ignored | Client wants a different key than `url` | Try `serverUrl` / `httpUrl` / `"type":"http"`, else stdio (§5) |
| TLS failure on `https://mcp.lh/mcp` | Root cert missing on that machine | Install it, or use `http://…:8099/mcp` |
| Every action returns 401 | Dashboard wants a control token the server lacks | Set `LECO_MCP_CONTROL_TOKEN` |
| Destructive call refused | Working as designed | §6 |

Live status for all of this is in the dashboard: **MCP → 1 · Server status**.

---

## Related

- [MCP_SERVER.md](MCP_SERVER.md) — every tool, response shaping, design rationale
- [ONBOARDING_COMPLEX_APPS.md](ONBOARDING_COMPLEX_APPS.md) — the evidence-driven onboarding flow
- [PRODUCTION_HARDENING.md](PRODUCTION_HARDENING.md) — before exposing any of this off the laptop
- [START_HERE.md](../START_HERE.md) — the route map
