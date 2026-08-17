---
name: diagnose
description: Root-cause a broken or degraded LEco DevOps stack — a 502, a 404, a crash-looping service, or an app that deploys but never answers. Argument is the symptom, hostname, app slug, or service name. Investigates only; changes nothing.
---

Diagnose the LEco DevOps stack. Reported symptom: **$ARGUMENTS**

Read the `leco:operate` skill and its `references/troubleshooting.md` before concluding
anything. Diagnose only — **run no destructive action, and do not redeploy to see if it
helps.** Restarting something "to check" is a change; propose it instead.

For a deep investigation that will burn many tool calls, delegate to the
`leco-diagnostician` subagent — it is read-only by construction and keeps the log dumps out
of this context.

## Establish the failure class first

Nearly every LEco incident is one of two things, and they have opposite fixes:

- **404 → there is no route.** Either the Traefik merge never happened for that app, or the
  manifest declares no routing, or the whole file provider is broken.
- **502 → the route exists but the backend is unreachable.** Almost always the container is
  not on `lh-network`, or Traefik's upstream name does not match Docker DNS.

`leco_traefik_routes(hostname="<host>")` settles it in one call. Do it early — it eliminates
half the search space.

## Sequence

1. `leco_server_info` — if the dashboard is unreachable, stop; nothing else can work, and
   that is the finding.
2. `leco_status(detail="services")` — level, missing services, alerts. 10–15 s; call once.
3. `leco_urls(only_unhealthy=True)` — what fails its probe. **If essentially everything
   fails, this is a global Traefik failure, not many app failures** — go to step 6.
4. For each failing service: `leco_logs(container, level="error", search=…)`. Use
   `leco_services` to map a friendly name to its container name. Always filter.
5. For a failing hosted app, in this order:
   - `leco_app_insights(slug)` — restart loops, error spikes, routing gaps. Cheapest signal.
   - `leco_app_validate(slug)` — manifest and paths; also heals a missing `lh-network`
     hosting overlay.
   - `leco_app_snapshot(slug, sections=["runtime","urls"])` — trust these fresh probes over
     the cached ones in the app list.
   - `leco_app_logs(slug, search=…)` last.
6. Global 404 across every hostname (including `dashboard.lh`) means Traefik's file provider
   dropped — typically a stale or invalid `hosting/traefik/01-stack-core.yml`. Confirm with
   `leco_traefik_routes()` returning little or nothing, then propose
   `leco_platform_traefik_apply()`.
7. `leco_help(search=…)` — full-text across the operator and developer manuals. Faster and
   more current than grepping the repository.

## Report

- **Root cause**, stated as one sentence about a mechanism, not a symptom.
- **The minimal fix**, as the specific tool call or command you propose to run. If it is
  destructive, say so and note it needs `confirm=true` **and** `LECO_MCP_ALLOW_DESTRUCTIVE=1`.
- **Evidence** — the two or three observations that rule out the alternatives.
- Anything you could **not** determine, said plainly rather than guessed.

If the cause is a production-only Cloudflare binding (`browser`, `vectorize`, `hyperdrive`,
`analytics_engine_datasets`, `send_email`, `mtls_certificates`) reporting `down`, say that it
is expected locally and has no fix — see `/leco:cf-local`.

## Without the MCP server

Say so first, then `./leco-cli.sh diagnose` (DNS, certs, Traefik files, `leco-devops`
presence), `docker logs traefik --tail 100`, and
`docker network inspect lh-network --format '{{range .Containers}}{{.Name}} {{end}}'` —
that last one answers the single most common 502 question. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs.
