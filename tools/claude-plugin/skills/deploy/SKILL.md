---
name: deploy
description: Deploy or redeploy one application on LEco DevOps and verify it actually answers on its *.lh hostname. Argument is the app slug, optionally followed by an action (deploy, restart, recreate, stop). Picks the right lifecycle action instead of redeploying blindly.
---

Deploy or redeploy a LEco DevOps hosted app.

Target: **$ARGUMENTS** (first argument is the app slug; an optional second argument is the
action).

Background for anything below: the `leco:operate` skill and its `references/`.

## 1. Resolve the slug, then find out *why* you are deploying

`leco_apps(query=…)` for the exact slug — never guess it.

Then, unless the user has just changed code on purpose, establish the reason before acting.
**A failed URL probe is not a reason to redeploy.** `leco_app_insights(slug)` and
`leco_app_validate(slug)` are both cheap and both tell you things a redeploy would not, and a
redeploy that "fixes" a routing problem is a coincidence you will pay for next time.

## 2. Pick the smallest action that does the job

| Situation | Action |
|-----------|--------|
| Code or image changed | `leco_app_control(slug, "deploy")` |
| Manifest, overlay compose, or bound dev stack changed | `leco_app_control(slug, "deploy")` — a restart will not re-read them |
| Container is wedged, config unchanged | `leco_app_control(slug, "restart")` |
| Compose definition changed but not the image | `leco_app_control(slug, "recreate")` — rebuilds containers, keeps volumes |
| Just need it off for a while | `leco_app_control(slug, "stop")` |
| Removing the app | not this skill — `/leco:offboard`, and it is destructive |

`deploy` and `stop` go through the `leco-devops` toolchain, so they also handle registration
and routing concerns; `restart` / `recreate` / `pause` / `unpause` are plain compose and do
not. That is exactly why a manifest change needs `deploy`.

`remove` and `reset` are available on this tool and are **destructive** (`reset` deletes data
volumes). They require `confirm=true` **and** `LECO_MCP_ALLOW_DESTRUCTIVE=1`, and the user's
agreement first. Do not reach for them to "clean up" a failing deploy.

Keep `stream=True` (the default) so a long build reports progress instead of timing out
silently.

## 3. Never fix it in the upstream repo

LEco-only concerns — `lh-network` membership, `ports: !reset []`, `*.lh` env defaults, local
edge runtimes — belong in overlay compose files under `hosting/app-available/<slug>/`,
referenced through `infrastructure.dockerCompose.additionalComposeFilesFromManifest`. If you
are about to edit someone's `docker-compose.yml`, you are solving it in the wrong place.

## 4. Verify — the tool returning success is not the finish line

`leco_app_snapshot(slug, sections=["runtime","urls"])`. A fresh deploy can take a moment to
answer; **one** retry is reasonable, a polling loop is not.

- **404** → the route is missing, not the app. The Traefik merge was skipped (register
  reports this and still exits successfully — the most common silent failure). Go to
  `/leco:routes`.
- **502** → the route is fine, the backend is unreachable, almost always because the
  containers are not on `lh-network`. `leco_app_validate(slug)` heals the hosting overlay;
  then `leco_app_control(slug, "recreate")`.
- **Container up, app erroring** → `leco_app_insights(slug)`, then `/leco:logs`.

## 5. Report

What you deployed, which action you chose and why, the resulting URL probe, and anything
still unresolved. If you had to apply a fix along the way, name it — a silent repair is a
problem that recurs.

## Without the MCP server

Say so first, then `./leco-cli.sh apps deploy <slug>` / `apps stop <slug>`, or `leco-devops
deploy` from inside the app directory with `LECO_ECOSYSTEM_ROOT` exported. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs, and note that the shell path
has no `confirm=` gate at all.
