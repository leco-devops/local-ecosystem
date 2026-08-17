---
name: offboard
description: Remove a hosted app from LEco DevOps — unregister it, strip its Traefik routes, and optionally delete its containers and data volumes. Argument is the app slug. DESTRUCTIVE — requires the user's explicit agreement first, confirm=true on the call, and a server started with LECO_MCP_ALLOW_DESTRUCTIVE=1.
---

Remove a hosted app from LEco DevOps.

Target: **$ARGUMENTS**

Background for anything below: the `leco:operate` skill and its `references/`.

## Stop. Ask the user before you call anything.

This skill deletes things. Before the first tool call, tell the user plainly:

- the **exact slug** you are about to offboard, confirmed from `leco_apps(query=…)` — never
  a guessed one, because a wrong-but-valid slug destroys the wrong app;
- the **hostname** that will stop answering;
- whether **data** is deleted (see the table below);
- what is **kept**.

Then wait for a clear yes. An implied instruction ("clean this up", "get rid of the old
one") is not agreement to delete data. If the user only wants the app *off*, they want
`leco_app_control(slug, "stop")`, which is reversible and not destructive — offer that first.

## Both safety gates apply, always

Every action below needs **`confirm=true`** on the call **and** a server started with
**`LECO_MCP_ALLOW_DESTRUCTIVE=1`**. They are independent on purpose: `confirm=true` stops a
volume being wiped as a side effect of a vague instruction, and the environment variable stops
it happening on a machine whose operator never opted in.

If a call returns **`Blocked destructive action`**, that is the product working correctly.
**Report the block and its reason to the user, and stop.** Do not achieve the same effect
another way — not with `leco_control` on the `leco-stack-<slug>` target, not with `docker`,
not by editing the registry or `hosting/traefik/dynamic.yml`, and not by dropping to
`./leco-cli.sh apps unregister`, **which has no gate at all**. Working around a block is
worse than the block, because the user's protection was deliberate.

## Choose the smallest action that does the job

| What the user actually wants | Call | Data |
|------|------|------|
| Off for now, back later | `leco_app_control(slug, "stop")` | kept — **not destructive, no gates** |
| Out of LEco, containers left alone | `leco_app_offboard(slug, confirm=True)` | kept |
| Out of LEco, containers gone | `leco_app_control(slug, "remove", confirm=True)` | volumes kept |
| Out of LEco, containers **and data** gone | `leco_app_control(slug, "reset", confirm=True)` | **deleted, unrecoverable** |

`leco_app_offboard` unregisters the app, strips its Traefik routes, and drops the registry
row. `remove` also offboards. `reset` additionally deletes the data volumes.

`leco_route_strip_keys` is **not** the teardown path — offboard strips routes for you. Reach
for it only to clean up an orphaned router that no app owns.

## What is not deleted

The app's files under `hosting/app-available/<slug>/` and the upstream repository itself are
**left in place** by all of these. Say so, so the user is not surprised either way — nothing
was lost, and nothing was cleaned up either.

## Verify afterwards

`leco_traefik_routes(hostname="<slug>.lh")` should no longer list it, and `leco_apps()`
should no longer return it. Report the slug removed, which action you used, what was deleted
versus kept, and the leftover files on disk.

## Related teardowns that are not this skill

- **Dev stack `reinstall` / `destroy`** → `/leco:dev-stack`. Same two gates, same rules.
- **Stack service `remove` / `reset`** → `/leco:up` covers lifecycle; the destructive
  variants carry the same gates and the same requirement to ask first.

## Without the MCP server

Say so first. `./leco-cli.sh apps offload <slug>` (compose down `-v` plus route strip) and
`./leco-cli.sh apps unregister <slug>` (full offboard) both execute **immediately, with no
confirmation prompt and no `LECO_MCP_ALLOW_DESTRUCTIVE` interlock**. In fallback mode *you*
are the entire safety layer: get an explicit yes in the conversation, quote back exactly what
will be deleted, and run `leco-devops offload --dry-run` first where it is available.
Mapping: `${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs.
