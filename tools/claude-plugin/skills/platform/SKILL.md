---
name: platform
description: Manage LEco DevOps platform configuration and platform services — enable or disable a service, start or stop it, inspect the profile/bundle/component catalog, change base domain or TLS mode, and reapply platform Traefik routes. Argument (optional) is a service id and action, or "config" / "catalog" / "traefik".
---

Manage the LEco DevOps platform layer.

Target: **$ARGUMENTS**

Background for anything below: the `leco:operate` skill and its `references/`.

## Platform services: three different meanings of "off"

`leco_platform_services()` lists them; `leco_platform_service_action(service_id, action)`
acts. The actions are not degrees of the same thing:

| Action | What it actually does |
|--------|-----------------------|
| `install` | Marks the service **enabled** in `config/leco-platform.yaml` |
| `disable` | Marks it **disabled** there — it stays off across stack starts |
| `start` / `stop` | Touches only the **running container**, right now |

So: "turn this off for good" is `disable`, not `stop`. "Turn it off until I need it" is
`stop`. And machine-wide container lifecycle for anything with a control target — including
services outside the platform layer — is `leco_control(target_id, action)` via `/leco:up`,
not this tool. Picking the wrong one produces a service that comes back on next boot, or one
that mysteriously never starts.

A target that "will not start with the stack" is very often policy-`offloaded` rather than
broken — check `leco_control_policies()` before treating it as a failure.

## Platform configuration — read, merge, write

`leco_platform_config()` with no argument is a safe read.

`leco_platform_config(set_config={…})` **replaces the whole file**. Never send a partial
object: read the current config, merge your change into it in memory, then write the merged
result back. A blind write silently drops every setting you did not include.

Changing `base_domain` or the TLS mode **re-addresses every route on the machine** — every
hosted app, every dev stack, the dashboard itself. Confirm with the user before doing it,
then follow with `leco_platform_traefik_apply()` and re-check `leco_urls()`, because the old
hostnames stop answering.

## Catalog

`leco_platform_catalog(section=…)` — `profiles`, `bundles`, `components`, `presets`, or
`all`. `presets` is the list `/leco:dev-stack` creates from; `components` is the
build-your-own list.

## Reapplying platform routes

`leco_platform_traefik_apply()` regenerates and applies the platform Traefik routes. This is
**the fix for a global 404** caused by a stale or invalid
`hosting/traefik/01-stack-core.yml`, where every hostname including `dashboard.lh` stops
resolving at once. Per-app routing lives in `/leco:routes` instead.

## Report

What changed, whether it changed *enablement* or only the *running container*, and what the
user must do for it to take effect (a stack start, a `traefik_apply`, a redeploy).

## Without the MCP server

Say so first, then `leco-devops platform show | services | presets | traefik-apply`, or
`./ecosystem-stack/ecosystem-stack.sh heal traefik` for the global-404 case. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs.
