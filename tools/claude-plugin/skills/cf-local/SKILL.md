---
name: cf-local
description: Check and repair the LEco DevOps Cloudflare-local adapters — R2, KV, D1, the Workers runtime and browser rendering — and tell a real failure apart from a production-only binding that is expected to read as down. Argument (optional) is an adapter or app slug to focus on.
---

Check the LEco DevOps Cloudflare-local layer.

Focus: **$ARGUMENTS**

`leco_cloudflare_local()` reports the health of the R2 / KV / D1 / Workers-runtime / browser
adapters in one call.

## Before calling anything down: is it *expected* to be down?

These bindings exist only in Cloudflare's production edge and have **no local equivalent**:

`browser` · `vectorize` · `hyperdrive` · `analytics_engine_datasets` · `send_email` ·
`mtls_certificates`

They will report `down` forever, and that is correct. Do not try to start them, do not
restart the stack over them, and do not tell the user something is broken. The fix is
declarative: list them under `infrastructure.runtimes[].productionOnlyBindings` in the app's
manifest so they stop counting as failures. Then re-validate — see `/leco:validate`.

## Backing stores come up before adapters

The adapters are fronts for two real services:

- `cf-minio` backs **R2**
- `cf-valkey` backs **KV**

If an adapter is down and its store is also down, the adapter is a *symptom*. Start the store
first, then the adapter — starting the adapter alone just fails again. Control ids are all
`cf-*`, with the bulk target `stack-cf-all`; discover them with
`leco_control_targets(group="cloudflare-local")` rather than guessing, and start them through
`/leco:up`.

## Per-app local bindings

An app's local KV / R2 / D1 resources are provisioned from its `wrangler.toml`. If a Worker
returns 404 for a route that should exist, or D1 queries error against a database the
dashboard says is healthy, the app's bindings were never provisioned — that is an app-level
step, not an adapter fault. `./leco-cli.sh apps provision <slug>` performs it.

Worker secret wiring is invisible to every tool here; `leco-devops runtimes -f leco.app.yaml`
is the only way to see `expected: N, wired: M, missing: …`.

The failure catalogue for Worker 404s, D1 errors and Varnish 503s is in the `leco:operate`
skill's `references/troubleshooting.md`.

## Report

Separate the three categories explicitly: **genuinely failing**, **down because its backing
store is down**, and **production-only, expected**. Collapsing them is how a healthy machine
gets reported as broken.

## Without the MCP server

Say so first, then `./leco-cli.sh cf <action>` and
`curl -s http://localhost:8090/api/overview | python3 -m json.tool`. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs.
