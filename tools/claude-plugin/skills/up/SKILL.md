---
name: up
description: Bring the LEco DevOps stack up in dependency order and confirm it is healthy, or take it back down cleanly. Argument (optional) is a scope — a control target id, or a group name such as ecosystem, infra, cloudflare-local, or file-transfer; with no argument, the ecosystem stack.
---

Bring the LEco DevOps stack up and confirm health.

Scope: **$ARGUMENTS** (if empty, the ecosystem stack).

Use the `leco:operate` skill for the dependency rules.

1. `leco_control_targets(running="no")` — see exactly what is down before touching anything.
   If everything is already up, say so and stop. Never guess a `target_id`; discover it here.
2. Start it:
   - **Cold start / whole stack:** `leco_control("stack-ecosystem-all", "start")`. The bulk
     target sequences dependencies internally — prefer it over starting targets one by one.
   - **Targeted recovery:** start individually, respecting order — `ai-traefik` first
     (nothing routes until the edge is up), then `ai-postgres` before `ai-n8n`, and
     `ai-paperclip-postgres` before `ai-paperclip`. `cf-minio` and `cf-valkey` before the
     R2/KV adapters. Hosted apps last.
   - Other bulk targets: `stack-infra-all`, `stack-cf-all`, `stack-file-transfer-all`.
   - Keep `stream=True` so you get live progress rather than a silent timeout.
3. `leco_status()` — repeat until the level settles. Give services time; do not restart
   something that is still starting.
4. `leco_urls(only_unhealthy=True)` — confirm the edge is actually serving.

Report what started, what stayed down, and **why** each one stayed down. A target that
refuses to start with the stack is often policy-`offloaded` rather than broken — check
`leco_control_policies()` before calling it a failure, and change policy with
`set_policies={…}` (merged into the existing map, not replacing it).

Do not run any destructive action (`remove`, `reset`) to "clean up" a stubborn service.
Report it instead, and hand off to `/leco:diagnose`.

## Bringing it back down

`leco_control("stack-ecosystem-all", "stop")`, or per-target in **reverse** order — apps and
dependents first, `ai-traefik` last, because stopping the edge first makes everything else
look broken while it drains. `stop` is not destructive and keeps containers and volumes.
`remove` and `reset` are a different thing entirely and are not part of stopping the stack.

To keep one service permanently out of stack start-up, set its policy to `offloaded` rather
than stopping it every time.

## Without the MCP server

Say so first, then `./leco-cli.sh stack start [service]` / `stack stop [service]` /
`stack deploy` (bulk sequence), same dependency order. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs. And note that
`./leco-cli.sh stack reset` deletes volumes **with no confirmation prompt**: in fallback mode
you are the safety layer.
