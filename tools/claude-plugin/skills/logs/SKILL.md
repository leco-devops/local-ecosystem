---
name: logs
description: Read logs for a LEco DevOps stack service, a hosted app, or a dev stack container, filtered so the answer is not buried. Argument is the service name, container name, or app slug, optionally followed by what to search for.
---

Read LEco DevOps logs.

Target: **$ARGUMENTS** (first argument identifies the service, container, or app; anything
after it is the search term).

Background for anything below: the `leco:operate` skill and its `references/`.

## 1. Work out what kind of thing you are looking at — the tools are different

| Target | How to resolve it | Tool |
|--------|-------------------|------|
| Stack service (`traefik`, `postgres`, `n8n`, `ollama`, …) | `leco_services` maps a friendly name → **container name** | `leco_logs(container, …)` |
| Hosted app | `leco_apps(query=…)` → **slug** | `leco_app_logs(slug, service=…)` |
| Dev stack component | `leco_dev_stack_snapshot(stack_id)` → container name | `leco_logs(container, …)` |

`leco_logs` wants a container name and will fail on a friendly name. `leco_app_logs` wants a
slug, and `service=""` covers **every** compose service in that app — which is what you want
when you do not yet know which one is failing.

## 2. For a hosted app, read the digest before the raw text

`leco_app_insights(slug)` already detects restart loops, error spikes, and routing gaps. It
is cheap and frequently makes the log read unnecessary. Start there.

## 3. Always filter

Both tools filter **server-side**: `search=…`, `level="error"|"warn"|"info"|"all"`,
`tail_lines`, `since_seconds` (default 1800). Use them.

An unfiltered dump is the classic waste: it consumes the response budget, gets truncated at
`LECO_MCP_MAX_RESPONSE_CHARS` (60 000 by default), and the interesting line is usually the
one that got cut. **If you hit truncation, the fix is a narrower filter, not a bigger cap.**

Two cases where the defaults are wrong:

- **Crash loop.** A restarting container's log window keeps resetting, so the failure that
  matters is older than it looks. Widen `since_seconds` and raise `tail_lines`, but keep
  `level="error"` so the payload stays readable.
- **Startup failure.** The cause is in the *first* lines after the restart, not the last —
  widen the window rather than increasing the tail.

## 4. Read them as evidence, not as an answer

A stack trace names the symptom. Say what mechanism produced it. If the log shows connection
refused to another service, check the dependency order (`ai-postgres` before `ai-n8n`,
`ai-paperclip-postgres` before `ai-paperclip`) before blaming the app.

Never repeat a credential, token, or connection string that appears in a log into your
report, a commit, or anything that leaves this machine. Refer to it by name.

## Without the MCP server

Say so first, then `./leco-cli.sh stack logs <service>` (**it follows — it will not exit on
its own**; prefer `docker logs <container> --tail 200` in an agent context) or
`./leco-cli.sh apps logs <slug>`. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs.
