---
name: urls
description: List the *.lh URLs LEco DevOps serves and report which ones are failing their probe, mapping each failure back to the app or service that owns it. Read-only. Argument (optional) is a category or hostname filter, or the word "failing" to list only unhealthy endpoints.
---

List LEco DevOps `*.lh` endpoints and their probe results.

Filter: **$ARGUMENTS** (if empty, list everything and highlight what fails).

1. `leco_urls()` — the whole address book. `category=…` narrows it.
2. `leco_urls(only_unhealthy=True)` — just the failures. This is the fastest "what is
   broken?" call in the whole tool set and is a better first diagnostic than `leco_status()`,
   which takes 10–15 seconds collecting live Docker stats.

Background for anything below: the `leco:operate` skill and its `references/`.

## The breadth of failure is the diagnosis

- **Essentially everything failing, `dashboard.lh` included** → one Traefik problem, not many
  app problems. The file provider dropped, usually from a stale or invalid
  `hosting/traefik/01-stack-core.yml`. Go to `/leco:routes`; do not investigate apps one by
  one.
- **One hostname failing** → that app. Classify it: **404 = no route**, **502 = route exists,
  backend unreachable**. Different fixes.
- **A cluster failing that share a backing service** → the backing service, not the routes.

## Two ways these results mislead

**Probes run inside Docker.** A `https://*.lh` URL is probed as `http://traefik<path>` with a
`Host: <app>.lh` header, because Traefik only listens on HTTP :80 inside the network. So a
probe reporting `HTTP 0` for a URL that works fine in your browser usually means a **stale
dashboard container**, not a broken app — restart `ai-dashboard` before investigating further.

**The reverse case is client-side.** If the probe passes but the browser fails, the platform
is serving correctly and the problem is local DNS resolution for `.lh` or certificate trust.
`./leco-cli.sh diagnose` checks both; nothing in the routing table needs changing.

## Attributing a failure to an owner

`leco_traefik_routes(hostname=…)` returns the routers and services Traefik has loaded **with
hosted-app ownership hints** — that is how you get from "this URL 404s" to "this app never
merged its routes". For a hosted app, follow with
`leco_app_snapshot(slug, sections=["runtime","urls"])`, whose probes are fresh; the
`main_url_probe` in `leco_apps` is cached and will disagree.

## Report

The endpoints grouped by owner, each failure with its status class and the most likely cause,
and one concrete next step. Read-only — repair belongs to `/leco:routes` or `/leco:deploy`.

## Without the MCP server

Say so first, then `./leco-cli.sh urls`, and check a single hostname with
`curl -sI -H "Host: myapp.lh" http://127.0.0.1/`. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs.
