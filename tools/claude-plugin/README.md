# LEco DevOps — Claude Code plugin (`leco`)

This plugin is part of the **LEco DevOps Open Project** and is released under the **MIT License** (see [../../LICENSE](../../LICENSE)).

It packages the **LEco DevOps MCP server** ([`tools/mcp-server/`](../mcp-server/)) together with the `/leco:*` **command set**, an always-available operations **skill**, and a read-only **diagnostician subagent**, so a Claude Code user can install one thing and immediately operate the platform competently.

The plugin identifier is **`leco`**, which is what makes the commands namespace as `/leco:status`, `/leco:deploy`, and so on. The product name is unchanged: **LEco DevOps**.

**Official repository:** [https://github.com/leco-devops/local-ecosystem](https://github.com/leco-devops/local-ecosystem)

---

## What you get

| Component | Name | Purpose |
|-----------|------|---------|
| **MCP server** | `leco-devops` | 65 tools over the LEco DevOps dashboard API — observability, control, hosted apps, onboarding, platform/dev stacks, routing, models, knowledge, credentials |
| **Skill** | `/leco:operate` | How the platform is wired, which tool to reach for, the order operations must happen in, and the failure modes that actually occur. Progressive disclosure: a tight `SKILL.md` plus five reference files loaded on demand. Auto-loads on LEco work |
| **Commands** | `/leco:*` | Eighteen workflow skills — see the table below |
| **Subagent** | `leco-diagnostician` | Read-only investigator. Burns the tool calls that diagnosis needs (logs, probes, snapshots) in its own context and returns a root cause plus a proposed minimal fix |

### Commands

| Command | What it does |
|---------|--------------|
| `/leco:status` | Read-only stack health report — services, `*.lh` probes, what is failing |
| `/leco:up` | Bring the stack up in dependency order, confirm health, and take it back down cleanly |
| `/leco:diagnose` | Root-cause a 502, a 404, a crash loop, or an app that deploys but never answers |
| `/leco:urls` | The `*.lh` address book, which endpoints fail, and who owns each failure |
| `/leco:logs` | Filtered logs for a stack service, a hosted app, or a dev stack container |
| `/leco:apps` | List and inspect hosted apps — registration state, runtime, URLs, manifests |
| `/leco:deploy` | Deploy or redeploy one app, choosing the right lifecycle action, then verify it |
| `/leco:validate` | Validate an app's manifest, profile and hosting wiring against schema and disk |
| `/leco:onboard` | Onboard an application repository end to end |
| `/leco:offboard` | Remove a hosted app — **destructive**, gated, asks first |
| `/leco:routes` | Audit and repair Traefik routing on `*.lh` |
| `/leco:platform` | Platform services on/off, platform config, catalog, reapply platform routes |
| `/leco:cf-local` | Cloudflare-local adapters — and telling a real failure from an expected one |
| `/leco:dev-stack` | Isolated dev stacks: create, start/stop, **repair before reinstall**, bind to an app |
| `/leco:models` | Local LLM models across the `ollama` and `airllm` runtimes |
| `/leco:credentials` | The UI credential vault — gated, and the values are plaintext secrets |
| `/leco:docs` | Search the platform's own operator/developer manuals and architecture docs |
| `/leco:mcp-server` | MCP server status, install, transports, and the two safety gates |

Every command is a skill under `skills/`, which is what produces the `/leco:<name>` form. A bare `/<name>` also works when nothing else claims it — but several of these last segments (`status`, `docs`, `logs`) are common words, so the namespaced form is the reliable one.

---

## Install

The MCP server is a Python package. Install it first — the plugin launches it, it does not vendor it.

```bash
# from the local-ecosystem repository root
pip install -e tools/mcp-server
leco-mcp doctor          # connectivity + configuration report; non-zero exit if unreachable
```

Then add this repository as a plugin marketplace and install the plugin:

```bash
claude plugin marketplace add /absolute/path/to/local-ecosystem
claude plugin install leco@leco-devops-open-project
```

> Give it the **absolute path**, not `./`. `marketplace add` resolves a relative path against your
> shell's working directory, and you are usually standing in the application you are onboarding
> rather than in this repository.

From another checkout or over the network, point the marketplace at the repository instead:

```bash
claude plugin marketplace add leco-devops/local-ecosystem
claude plugin install leco@leco-devops-open-project
```

> This clones the repository's **default branch**. Until the plugin is merged there, it fails with
> `Marketplace file not found at …/.claude-plugin/marketplace.json` — which means *that branch does
> not carry the plugin*, not that anything is broken. `marketplace add` has no `--branch` flag, so
> use the local-path form until the merge lands. Check with:
>
> ```bash
> curl -s -o /dev/null -w '%{http_code}\n' \
>   https://raw.githubusercontent.com/leco-devops/local-ecosystem/main/.claude-plugin/marketplace.json
> ```

Verify:

```bash
claude plugin details leco@leco-devops-open-project      # component inventory + token cost
claude mcp list                                          # the leco-devops server should report Connected
```

To remove:

```bash
claude plugin uninstall leco@leco-devops-open-project
claude plugin marketplace remove leco-devops-open-project
```

### Upgrading from the old `leco-devops` plugin

Versions before 0.2.0 installed as `leco-devops` and produced flat commands (`/leco-status`, `/leco-up`, …). Uninstall the old identifier before installing the new one, or both appear in the command list:

```bash
claude plugin uninstall leco-devops@leco-devops-open-project
claude plugin marketplace update leco-devops-open-project
claude plugin install leco@leco-devops-open-project
```

### Project-scoped alternative (no plugin)

Working inside this repository, [`.mcp.json`](../../.mcp.json) at the repository root already registers the same server for project scope. Claude Code prompts for approval on first use. That gives you the tools without the skills, commands, or subagent.

---

## Configuration

The plugin launches the server through [`bin/leco-mcp-launch`](bin/leco-mcp-launch), which resolves `leco-mcp` in this order:

1. `$LECO_MCP_BIN` — an explicit path to an executable (use this for a virtualenv install);
2. `leco-mcp` on `PATH`;
3. `python -m leco_mcp` when the package is importable;
4. the repository source tree — next to the plugin, or under `$LECO_ECOSYSTEM_ROOT` / `$CLAUDE_PROJECT_DIR`.

If none resolve, it exits with the install command rather than failing silently. Override the interpreter with `$LECO_MCP_PYTHON`.

Environment variables read by the server (full reference: [`docs/MCP_SERVER.md`](../../docs/MCP_SERVER.md)):

| Variable | Default | Effect |
|----------|---------|--------|
| `LECO_MCP_DASHBOARD_URL` | `http://localhost:8090` | Dashboard base URL. `http://dashboard.lh` also works |
| `LECO_MCP_CONTROL_TOKEN` | — | Matches the dashboard's `DASHBOARD_CONTROL_TOKEN`. Without it, control actions return 401 when the dashboard enforces a token |
| `LECO_MCP_ALLOW_DESTRUCTIVE` | `0` | Required, in addition to `confirm=true`, for `remove` / `reset` / `destroy` / `reinstall` / offboard / route-strip / model delete / credential reset |
| `LECO_MCP_ALLOW_CREDENTIALS` | `0` | Required for the UI credential-vault tools, which return plaintext local-dev secrets |
| `LECO_MCP_READ_TIMEOUT` · `LECO_MCP_ACTION_TIMEOUT` | `120` · `900` | Seconds |
| `LECO_MCP_VERIFY_TLS` · `LECO_MCP_MAX_RESPONSE_CHARS` | `1` · `60000` | TLS verification; response truncation cap |

The bundled [`.mcp.json`](.mcp.json) passes these through with safe defaults, so anything already set in your shell wins and both gates stay **off** unless you deliberately open them.

**Both destructive gates are required by design.** `confirm=true` stops an agent wiping a volume as a side effect of a vague instruction; `LECO_MCP_ALLOW_DESTRUCTIVE=1` stops it happening on a machine whose operator never opted in. A blocked call is the product working correctly — every skill that can reach a gated action instructs the agent to report the block rather than route around it.

---

## Layout

```
tools/claude-plugin/
  .claude-plugin/plugin.json      # plugin manifest — "name": "leco" is what namespaces the commands
  .mcp.json                       # bundled MCP server (${CLAUDE_PLUGIN_ROOT}/bin/leco-mcp-launch)
  bin/leco-mcp-launch             # POSIX sh launcher with four-step resolution
  bin/validate-skills             # asserts every SKILL.md uses only the six legal frontmatter keys
  skills/operate/                 # the knowledge skill — /leco:operate
    SKILL.md                      # operating rules and workflows
    references/architecture.md    # how the platform is wired
    references/troubleshooting.md # failure modes, causes, minimal fixes
    references/onboarding.md      # app onboarding playbook
    references/tool-map.md        # tool selection, cost model, safety gates
    references/cli-fallback.md    # operating without the MCP server
  skills/{status,up,diagnose,urls,logs}/SKILL.md          # observe and recover
  skills/{apps,deploy,validate,onboard,offboard}/SKILL.md # hosted apps
  skills/{routes,platform,cf-local}/SKILL.md              # edge, platform, Cloudflare-local
  skills/{dev-stack,models,credentials}/SKILL.md          # dev stacks, LLMs, the vault
  skills/{docs,mcp-server}/SKILL.md                       # documentation, and the tools themselves
  agents/leco-diagnostician.md
```

`skills/` and `agents/` are auto-discovered from the plugin root; only `mcpServers` is declared in the manifest. The marketplace entry lives in [`.claude-plugin/marketplace.json`](../../.claude-plugin/marketplace.json) at the repository root.

There is no `commands/` directory. Claude Code merged custom commands into skills, and only the `skills/` layout produces the namespaced `/<plugin>:<name>` form — a `commands/*.md` file would come back as a flat `/leco-status`.

**One `references/` directory, deliberately.** The dashboard's install panel identifies the knowledge skill as *whichever skill ships reference files* ([`dashboard/mcp_insights.py`](../../dashboard/mcp_insights.py)). Keep `references/` under `skills/operate/` only; a second one makes that detection ambiguous.

---

## Degraded mode

Every skill works without the MCP server. When the tools are unavailable each one says so first, then falls back to `./leco-cli.sh`, `./ecosystem-stack/ecosystem-stack.sh`, `leco-devops`, or direct dashboard API calls. The full mapping lives in one place — `skills/operate/references/cli-fallback.md` — and the command skills point at it rather than duplicating it.

Two hazards that file records and every skill repeats:

- **`./leco-cli.sh` with no subcommand opens an interactive menu and hangs** waiting for a keypress. Always pass a subcommand.
- **The shell path has no confirmation gate.** `stack reset`, `apps unregister`, and `dev-stack destroy` delete data immediately, with no `confirm=` interlock and no `LECO_MCP_ALLOW_DESTRUCTIVE` check. In fallback mode the agent is the entire safety layer.

---

## Validate after editing

```bash
bin/validate-skills                                       # six-key frontmatter check across every SKILL.md
claude plugin validate --strict tools/claude-plugin       # manifest
claude plugin validate --strict tools/claude-plugin/skills
claude plugin validate --strict tools/claude-plugin/agents
claude plugin validate --strict .                         # marketplace manifest
claude plugin marketplace update leco-devops-open-project # pick up local changes
claude plugin details leco@leco-devops-open-project       # the actual component inventory
```

Run `bin/validate-skills` first and treat `claude plugin details` as the real proof. `claude plugin validate --strict` passes on a plugin whose skills all fail to load: a single illegal frontmatter key — `argument-hint` is the one that bites after the commands-into-skills migration — drops that skill silently, and only the details inventory shows it missing.

---

## Related documentation

- [`docs/MCP_SERVER.md`](../../docs/MCP_SERVER.md) — MCP server architecture, tool surface, transports
- [`tools/mcp-server/`](../mcp-server/) — the server itself (`leco_mcp`, distribution `leco-mcp`)
- [`docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md`](../../docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md) — 502, `lh-network`, Docker DNS, local edge runtimes
- [`docs/LECO_APP_BLUEPRINT.md`](../../docs/LECO_APP_BLUEPRINT.md) — bridge vs profile (v3), hosting overlays, teardown
- [`docs/DEV_STACK_ISOLATION.md`](../../docs/DEV_STACK_ISOLATION.md) — isolated dev stacks and lifecycle semantics
- [`docs/DEPLOY_CLI.md`](../../docs/DEPLOY_CLI.md) — the `leco-devops` CLI
- [`AGENTS.md`](../../AGENTS.md) — repository agent guide and change-together module rules
