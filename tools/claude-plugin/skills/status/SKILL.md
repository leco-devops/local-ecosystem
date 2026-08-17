---
name: status
description: Report LEco DevOps stack health — services, *.lh URL probes, and anything currently failing. Read-only. Argument (optional) is a single service name, app slug, or hostname to focus on; with no argument, report on the whole stack.
---

Report the current health of the LEco DevOps stack.

Focus: **$ARGUMENTS** (if empty, report on the whole stack).

Use the `leco:operate` skill for context on what these results mean.

1. `leco_server_info` — confirm the dashboard answers, and note whether a control token is
   configured and which safety gates are open. If it is unreachable, stop and say so; every
   other tool will fail too, and the fix is `/leco:mcp-server`, not more probing.
2. `leco_status(detail="services")` — health level, service counts, alerts. This collects
   live Docker stats and takes 10–15 seconds; call it once, never in a loop.
3. `leco_urls(only_unhealthy=True)` — the endpoints that failed their probe.
4. If a focus argument was given, narrow with `leco_traefik_routes(hostname=…)`,
   `leco_apps(query=…)`, or `leco_app_snapshot(slug, sections=["runtime","urls"])`.

Then report, in this order:

- **Headline:** overall health level in one line.
- **Down or failing:** each item, with the container/target id and the most likely cause.
  Distinguish **404 (no route)** from **502 (route exists, backend unreachable)** — they
  have different fixes.
- **Worth knowing:** anything degraded but not failing (offloaded targets, pending
  registrations, unread updates from `leco_updates(unread_only=True)`).
- **Suggested next step:** one concrete action, not a menu.

Do not start, stop, or repair anything. This is a read-only report. If a fix is obvious,
propose it and wait — `/leco:up` starts things, `/leco:diagnose` investigates them.

## Without the MCP server

Say so first, then use `./leco-cli.sh diagnose` (alias `doctor`), which is the closest
analogue to steps 2 and 3 together. Full mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs.
