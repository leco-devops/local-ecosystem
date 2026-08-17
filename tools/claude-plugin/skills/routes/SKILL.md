---
name: routes
description: Inspect and repair Traefik routing on *.lh — diff the routes a manifest implies against the routes Traefik has actually loaded, and fix a global 404, a single app that 404s, or a stale fragment. Argument (optional) is a hostname or app slug to focus on; with no argument, audit the whole routing table.
---

Inspect and, if needed, repair LEco DevOps Traefik routing.

Target: **$ARGUMENTS** (if empty, audit the whole routing table).

Read `references/architecture.md` §1 and `references/troubleshooting.md` §§1–4 in the
`leco:operate` skill before changing anything.

## Where routes live

| File | Owner | Meaning |
|------|-------|---------|
| `traefik/dynamic.yml` | git | Canonical stack routes. Editing it changes nothing at runtime on its own |
| `hosting/traefik/01-stack-core.yml` | generated | Copy of the above, refreshed on Traefik start. **Stale or invalid here = global 404** |
| `hosting/traefik/dynamic.yml` | generated | Writable merge target for hosted apps |
| `hosting/traefik/20-dev-stacks.yml` | generated | Dev stack routes |

Traefik watches `hosting/traefik/` as a file provider and hot-reloads. An empty or invalid
`http:` key in **any** file there makes it drop the whole provider — every hostname 404s at
once, `dashboard.lh` included.

## Audit

1. `leco_traefik_routes(hostname=…)` — what Traefik has **loaded**.
2. For a hosted app, `leco_route_fragment_from_app(slug)` — what the manifest **implies**.
   This is read-only.
3. Diff them. Check the upstream host in each service URL against the app's real container
   name: the compose `container_name` when set, otherwise
   `{dockerCompose.projectName}-{service}-1`.
4. `leco_urls(only_unhealthy=True)` to see which hostnames actually fail.

## Repair, choosing by symptom

- **Global 404, every hostname** → the file provider is broken or `01-stack-core.yml` is
  stale. `leco_platform_traefik_apply()`. CLI fallback:
  `./ecosystem-stack/ecosystem-stack.sh heal traefik`.
- **One app 404s** → the Traefik merge never happened. Re-register with
  `leco_register(path, app_id, deploy=False)`, or merge the fragment directly with
  `leco_route_merge_fragment(yaml_fragment)` (additive; same-named keys replaced, everything
  else kept).
- **One app 502s** → the route is fine, the backend is not reachable on `lh-network`. This is
  not a routing fix. Run `leco_app_validate(slug)` and see `references/troubleshooting.md` §1.
- **Manifest itself is wrong** → fix the manifest and re-register. Do not paper over it with
  a hand-written fragment; the next register will overwrite it.

Never hand-set router `priority` values. Priority is derived from path-prefix length, so a
longer prefix (`/health/json`) automatically outranks a shorter one (`/api`, then `/`).

`leco_route_strip_keys` deletes routers and services and takes an app offline at its
hostname. It is destructive: it requires `confirm=true` **and** `LECO_MCP_ALLOW_DESTRUCTIVE=1`,
and it needs the user's agreement first. Normal teardown goes through `/leco:offboard`, which
strips routes for you — prefer that. If a call is blocked, report the block; do not hand-edit
`hosting/traefik/dynamic.yml` to achieve the same effect.

Report which routes were missing, wrong, or extra, what you changed, and the URL probe result
afterwards.

## Without the MCP server

Say so first, then `./leco-cli.sh traefik heal` / `traefik ensure-files` for the global-404
fix, `./leco-cli.sh apps register <slug>` to re-merge one app, and
`./leco-cli.sh apps fragment <slug>` to print the implied fragment. Verify a hostname with
`curl -sI -H "Host: myapp.lh" http://127.0.0.1/`. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs.
