---
name: apps
description: List and inspect the applications hosted on LEco DevOps — inventory, registration state, container runtime, *.lh URLs, manifests and attached services. Read-only. Argument (optional) is an app slug or a search term to focus on; with no argument, list everything.
---

List and inspect LEco DevOps hosted apps.

Focus: **$ARGUMENTS** (if empty, the whole inventory).

Background for anything below: the `leco:operate` skill and its `references/`.

## Inventory first, detail second

1. `leco_apps(query=…, running=…)` — cheap. This is the only reliable source of **slugs**;
   never guess one, and never derive it from a directory name.

   Two things in this payload are routinely misread:

   - **`pending_registration=true`** means the app is materialized on disk but was never
     registered. It therefore has no Traefik route and its hostname 404s. That is a *state*,
     not a fault — the fix is `leco_register(...)` / `/leco:onboard`, not debugging.
   - **`main_url_probe` here is cached.** Do not report it as current health. If it matters,
     get a fresh probe in step 2.

2. `leco_app_snapshot(slug, sections=[…])` — the detail view. Sections are `identity`,
   `runtime`, `urls`, `manifest`, `services`, `data`; the default is identity + runtime +
   urls. **Ask for the sections you need and never `full=True`** — the full payload is large
   enough to hit the response cap and push out the answer.

   A snapshot's `url_probes` are **fresh**. When they disagree with the cached probe from
   step 1, the snapshot wins — they disagree by design, not by bug.

3. `leco_app_insights(slug)` — restart loops, error spikes, routing gaps, pre-digested.
   Cheap, and it usually answers "is this app healthy?" without reading a single log line.
   Call it before `/leco:logs`.

4. `leco_app_metrics(slug, limit=…)` — per-app CPU / memory / network time series, for
   "is it leaking / is it saturating", not for "is it up".

## Mapping between the identifiers

An app has a **slug** (registry + hostname `<slug>.lh`), a **control target** named
`leco-stack-<slug>` in `leco_control_targets`, and one or more **compose services** whose
container names appear in the snapshot's `services` section. Different tools want different
ones. Get them from the snapshot rather than constructing them.

## Report

Lead with the answer to what was asked, then: which apps are running, which are registered
but down, which are pending registration, and which have a failing main URL — with the
**404 vs 502** distinction, because they have different fixes.

Do not start, stop, or deploy anything here. `/leco:deploy` changes state, `/leco:diagnose`
investigates a failure, `/leco:validate` checks the wiring.

## Without the MCP server

Say so first, then `./leco-cli.sh apps list` and `./leco-cli.sh apps status <slug>`, or
`curl -s http://localhost:8090/api/hosted-apps | python3 -m json.tool` for the same data the
tools read. Mapping: `${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`.
Never invoke `./leco-cli.sh` bare — it opens an interactive menu and hangs.
