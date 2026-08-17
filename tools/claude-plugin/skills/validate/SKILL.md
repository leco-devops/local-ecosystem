---
name: validate
description: Validate a LEco DevOps app's manifest, profile and hosting wiring against the schema and what is actually on disk — the right check when an app deploys but does not route. Argument is the app slug, or a path when validating a manifest that is not registered yet.
---

Validate a LEco DevOps app's configuration and wiring.

Target: **$ARGUMENTS**

Background for anything below: the `leco:operate` skill and its `references/`.

## For a registered app

`leco_app_validate(slug)` — checks the manifest and profile against the schema and against
the paths that actually exist on disk. **This is the call to make when an app deploys but
does not route**, and it is cheap enough to make before reading any logs.

Know two things about it:

- **It is not purely a check.** It auto-heals a missing `lh-network` hosting overlay. So
  after it reports a heal, the containers still need `leco_app_control(slug, "recreate")` to
  actually join the network — validating alone changes nothing that is already running.
- **It does not test reachability.** It cannot tell you whether the upstream service is
  really listening on the declared port, and it does not look at Traefik's loaded routing
  table. For that, diff `leco_route_fragment_from_app(slug)` (what the manifest implies)
  against `leco_traefik_routes(hostname=…)` (what Traefik has) — see `/leco:routes`.

## For a manifest you are editing, before it is saved

`leco_manifest_validate(manifest_yaml=…, localhost_yaml=…, path=…)` validates content that is
not yet on disk. Invalid YAML is rejected rather than written, so validate in the edit loop:
`leco_manifest_read` → edit → `leco_manifest_validate` → `leco_manifest_save`.

`leco_manifest_status(path, app_id)` before `leco_manifest_generate` — **generate overwrites
existing LEco manifests**, hand edits included.

## What none of these cover

- **Worker secret wiring.** `leco-devops runtimes -f leco.app.yaml` is the only way to see
  `expected: N, wired: M, missing: …`.
- **Production-only Cloudflare bindings.** `browser`, `vectorize`, `hyperdrive`,
  `analytics_engine_datasets`, `send_email`, `mtls_certificates` have no local equivalent and
  will read as `down` forever. Declare them under
  `infrastructure.runtimes[].productionOnlyBindings` so they stop counting as failures —
  do not try to make them pass. See `/leco:cf-local`.
- **Whether the app works.** Validation is about wiring. `leco_app_insights(slug)` and a
  fresh `leco_app_snapshot(slug, sections=["runtime","urls"])` are about behaviour.

## Report

Each finding with the field or path it concerns, whether validation healed anything (and so
whether a `recreate` is now required), and what remains wrong. If the manifest itself is
wrong, fix the manifest and re-register — do not compensate with a hand-written Traefik
fragment that the next register will overwrite.

## Without the MCP server

Say so first, then `leco-devops runtimes -f leco.app.yaml` and `leco-devops status` from
inside the app directory, plus `./leco-cli.sh apps fragment <slug>` to see the implied routes.
Mapping: `${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs.
