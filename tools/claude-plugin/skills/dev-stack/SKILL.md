---
name: dev-stack
description: Create, start, stop, repair and bind LEco DevOps isolated dev stacks — WordPress, Magento, Postgres/Redis component stacks and the rest of the preset catalog. Argument (optional) is a dev stack id, optionally followed by an action; with no argument, list what exists and what can be created.
---

Work with LEco DevOps isolated dev stacks.

Target: **$ARGUMENTS**

Background for anything below: the `leco:operate` skill and its `references/`.

## See what exists, and what could exist

- `leco_dev_stacks()` — the stacks on this machine.
- `leco_platform_catalog(section="presets")` — the one-click presets `leco_dev_stack_create`
  accepts. `section="components"` lists the individual services for a build-your-own stack.
- `leco_dev_stack_snapshot(id)` — one stack in detail, including container names (which is
  what `/leco:logs` needs).

## Creating

`leco_dev_stack_create(stack_id, preset=… | template=… | components=[…], sample_data=…)` —
**exactly one** of `preset`, `template`, `components`. More than one is an error, none is an
error.

Two things people get wrong:

1. **Creating does not start it.** Follow with `leco_dev_stack_action(id, "start")`.
2. **The `stack_id` is load-bearing.** It becomes the generated hostnames and volume names.
   Agree it with the user before creating; renaming later means recreating.

## The action ladder — climb it in order, never skip to the top

`start`, `stop`, `redeploy` are ordinary.

**`repair` first, always.** It fixes images, generated routing, and `lh-network` attachment
*in place*, keeping volumes **and** manual edits made through `leco_dev_stack_files`. Most
"my dev stack is broken" reports end here.

**`reinstall`** rebuilds from the template. It **wipes data** and **reverts every manual
edit**. It is only correct when the template configuration itself is wrong — not as a
stronger repair.

**`destroy`** removes the stack and its data entirely.

`reinstall` and `destroy` are destructive. Both gates apply: `confirm=true` on the call
**and** a server started with `LECO_MCP_ALLOW_DESTRUCTIVE=1`. **Ask the user first** — name
the stack, say that data is deleted and manual edits reverted, and wait for a clear yes. If
the call comes back `Blocked destructive action`, **report the block and its reason to the
user and stop**; do not achieve the same effect with `leco_control`, with `docker`, by
editing files, or by dropping to `leco-devops dev-stack destroy` in the shell, which has no
gate at all.

## Config files and credentials

- `leco_dev_stack_files(id, path=…, write_content=…)` lists, reads and writes the generated
  config. Changes need a `redeploy` to apply — and `reinstall` reverts them.
- `leco_dev_stack_access(id)` returns **credentials**. Treat them as radioactive: refer to
  them by name, never put them in a report, a commit, a log, or anything that leaves the
  machine. `leco_dev_stack_reset_admin(id)` issues fresh template admin credentials
  (WordPress, Magento, …).

## Binding a dev stack to a hosted app

`leco_app_bind_dev_stack(slug, dev_stack_id)` writes `platform.devStackId` into the app's
manifest. **Nothing changes until the app is redeployed** — follow with
`leco_app_control(slug, "deploy")` (`restart` will not re-read the manifest). Pass
`dev_stack_id=""` to unbind.

## Without the MCP server

Say so first, then `leco-devops dev-stack list | create | start | repair | snapshot | access`.
Mapping: `${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. In the shell
`dev-stack reinstall -y` and `dev-stack destroy` execute immediately with **no confirmation
gate** — in fallback mode you are the safety layer, so get an explicit yes in the
conversation first.
