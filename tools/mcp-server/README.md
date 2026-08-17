# LEco DevOps MCP server

**Model Context Protocol access to LEco DevOps** — lets Claude Code and other MCP clients
deploy applications, onboard repositories, turn infrastructure on and off, and monitor the
stack, with the same semantics as the dashboard UI.

> Part of the **[LEco DevOps Open Project](https://github.com/leco-devops/local-ecosystem)** ·
> MIT · Full guide: **[`docs/MCP_SERVER.md`](../../docs/MCP_SERVER.md)**

## What it does

Every tool calls the LEco DevOps dashboard REST API. The dashboard stays the single source
of truth for lifecycle, registry, and routing semantics — this server adds no independent
Docker access, no shell execution, and no second copy of the rules.

```
Claude Code ──stdio──┐
                     ├── leco-mcp ──HTTP──► LEco DevOps dashboard ──► Docker · Traefik · leco-devops
Remote agent ──HTTP──┘
```

## Install

```bash
# From the repo root — stdio transport for local Claude Code
pipx install ./tools/mcp-server          # or: uv tool install ./tools/mcp-server
leco-mcp doctor                          # connectivity + configuration report
```

**Need pipx first?** It does not ship with Python:

| Platform | Command |
|---|---|
| macOS | `brew install pipx` |
| Debian / Ubuntu 23.04+ | `sudo apt install pipx` |
| Fedora / RHEL | `sudo dnf install pipx` |
| Arch | `sudo pacman -S python-pipx` |
| Anywhere else | `python3 -m pip install --user pipx` |
| Windows (WSL2) | Use your distro's row, inside the WSL shell |
| Windows (native) | `py -m pip install --user pipx` |

Then `pipx ensurepath` and open a new shell so `~/.local/bin` is on `PATH`.

> Do **not** reach for `pip install -e .` instead. A Homebrew or distro Python refuses it under
> PEP 668 with `externally-managed-environment`. If you want no new tooling, use a venv:
> `python3 -m venv .venv && .venv/bin/pip install -e .`, then point `LECO_MCP_BIN` at
> `.venv/bin/leco-mcp`.

Register it with Claude Code:

```bash
claude mcp add leco-devops -- leco-mcp stdio
```

For a shared HTTP endpoint (remote agents, other machines), run it as a stack service:

```bash
./ecosystem-stack/services/mcp.sh start   # https://mcp.lh/mcp
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `LECO_MCP_DASHBOARD_URL` | auto-discovered | Dashboard base URL. Tries `localhost:8090`, `dashboard.lh`, then the in-network name |
| `LECO_MCP_CONTROL_TOKEN` | — | Control token; also read from `DASHBOARD_CONTROL_TOKEN`. Required only if the dashboard enforces one |
| `LECO_MCP_ALLOW_DESTRUCTIVE` | `0` | Enables `remove` / `reset` / `destroy` / offboard tools |
| `LECO_MCP_ALLOW_CREDENTIALS` | `0` | Enables the UI credential vault tools |
| `LECO_MCP_READ_TIMEOUT` | `120` | Seconds for read calls (`/api/overview` collects live Docker stats and is slow) |
| `LECO_MCP_ACTION_TIMEOUT` | `900` | Seconds for deploys and other streaming actions |
| `LECO_MCP_MAX_RESPONSE_CHARS` | `60000` | Hard cap on any tool result |
| `LECO_MCP_VERIFY_TLS` | `1` | Set `0` for `*.lh` hosts whose mkcert root is not trusted by this process |
| `LECO_MCP_HTTP_HOST` / `_PORT` / `_PATH` | `127.0.0.1` / `8099` / `/mcp` | HTTP transport bind |
| `LECO_MCP_PROJECT_ROOT` | repo root (`/project` in the container) | Where generated files live |
| `LECO_MCP_ACTIVITY_LOG` | `<project root>/ecosystem-stack/config/generated/mcp-activity.jsonl` | Activity log path. Set to an empty string to disable telemetry |
| `LECO_MCP_ACTIVITY_MAX_EVENTS` | `2000` | Events kept in the log; it is trimmed once it exceeds ~1.5× this |

## Activity telemetry

Every tool call and session boundary is appended to a JSONL file so the dashboard can show
which agent is doing what. One JSON object per line, always these keys:

```json
{"ts": "2026-08-17T10:00:00.000000+00:00", "event": "tool_call", "session_id": "abc123",
 "transport": "stdio", "client": {"name": "claude-code", "version": "2.0.1"},
 "tool": "leco_control", "args_summary": {"target_id": "ai-ollama", "action": "start"},
 "target": {"kind": "service", "id": "ai-ollama"}, "ok": true, "error": null,
 "blocked": false, "destructive": false, "duration_ms": 1234}
```

`event` is `session_start`, `tool_call`, or `session_end`; session events carry `null` for
the call-scoped half. `blocked: true` means a safety gate refused the call — deliberately
distinct from `ok: false`, which is a call that ran and failed.

Arguments are recorded through a **name whitelist of scalars** (`leco_mcp/activity.py`), so
credential maps, manifest bodies, and file contents have no route into the log. Writing is
best-effort: a bad path or a read-only mount disables telemetry rather than failing a call.

Over HTTP, `GET /insights` returns the same data live (server info, active sessions, recent
events, counts) from memory:

```bash
curl -s http://localhost:8099/insights | jq .
```

`leco-mcp doctor` reports the log path, whether it is writable, and the current event count.

## Safety model

Destructive operations are behind **two independent gates**:

1. the call must pass `confirm=true`, and
2. the server must have been started with `LECO_MCP_ALLOW_DESTRUCTIVE=1`.

Neither alone is enough. An agent cannot delete a volume as a side effect of a vaguer
instruction, and an operator who never sets the environment variable cannot be talked into
it by a prompt. Credential tools have their own gate, `LECO_MCP_ALLOW_CREDENTIALS=1`,
because they return plaintext local-dev secrets.

Blocked calls fail with a message naming both gates, so the agent can report the block
accurately instead of hunting for a workaround.

## Tools

65 tools across ten families. Read-only tools carry `read_only_hint`; lifecycle tools
carry `destructive_hint`.

| Family | Tools |
|--------|-------|
| **Observe** | `leco_server_info` `leco_status` `leco_services` `leco_logs` `leco_urls` `leco_metrics` `leco_cloudflare_local` `leco_traefik_routes` `leco_version` |
| **Control** | `leco_control_targets` `leco_control` `leco_control_policies` |
| **Hosted apps** | `leco_apps` `leco_app_snapshot` `leco_app_control` `leco_app_logs` `leco_app_insights` `leco_app_metrics` `leco_app_validate` `leco_app_bind_dev_stack` `leco_app_data_import_plan` `leco_app_data_import` `leco_app_offboard` `leco_verify` `leco_certs_refresh` |
| **Onboarding** | `leco_browse` `leco_detect` `leco_app_evidence` `leco_compose_validate` `leco_manifest_status` `leco_manifest_generate` `leco_manifest_read` `leco_manifest_validate` `leco_manifest_overlay` `leco_manifest_save` `leco_manifest_urls` `leco_manifest_samples` `leco_register` `leco_onboard` |
| **Platform** | `leco_platform_config` `leco_platform_catalog` `leco_platform_services` `leco_platform_service_action` `leco_platform_traefik_apply` |
| **Dev stacks** | `leco_dev_stacks` `leco_dev_stack_create` `leco_dev_stack_action` `leco_dev_stack_snapshot` `leco_dev_stack_access` `leco_dev_stack_files` `leco_dev_stack_reset_admin` |
| **Routing** | `leco_route_fragment_from_app` `leco_route_merge_fragment` `leco_route_strip_keys` |
| **AI models** | `leco_llm_models` `leco_llm_model_action` `leco_llm_model_inspect` `leco_llm_catalog` |
| **Knowledge** | `leco_docs` `leco_help` `leco_updates` `leco_updates_mark_read` |
| **Credentials** | `leco_ui_credentials` `leco_ui_credentials_set` `leco_ui_credentials_reset` |

Prompts: `onboard_app`, `diagnose_stack`, `bring_up_stack`.

### The composite tool

`leco_onboard(path, app_id)` runs the whole path — detect → generate manifest → register →
deploy → verify — and reports each stage separately so a failure is attributable. The step
tools remain available for apps that need hand-tuned routes or ports.

## Response shaping

Dashboard payloads are built for a browser: `/api/overview` is ~56 KB and takes 10-15
seconds because it collects live Docker stats. Tools return compact views by default and
expose `detail` / `sections` / `full` knobs, with a hard character cap as a backstop. Prefer
the defaults and narrow with filters rather than pulling full payloads.

## Development

```bash
pip install -e './tools/mcp-server[dev]'
python -m pytest tools/mcp-server/tests -q
leco-mcp doctor
```

The tests cover the safety gates, response shaping, configuration resolution, and server
assembly without needing a running stack.

## Layout

```
tools/mcp-server/
  leco_mcp/
    __main__.py     CLI: stdio | http | doctor
    server.py       server assembly + client-facing instructions
    config.py       environment configuration
    client.py       dashboard HTTP client, base-URL discovery, NDJSON streaming
    safety.py       destructive / credential gates
    shaping.py      response compaction
    runtime.py      shared deps + stream consumption
    prompts.py      workflow prompts
    tools/          one module per tool family
  Dockerfile        HTTP transport image (build context = repo root)
  tests/
```
