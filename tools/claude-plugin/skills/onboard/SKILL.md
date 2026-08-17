---
name: onboard
description: Onboard an application repository onto LEco DevOps end to end — detect, generate the manifest, register, merge Traefik routes, deploy, and verify the *.lh hostname actually answers. Arguments are the app path in rooted form (e.g. wsp:MyApp) and, optionally, the app id to use as the slug and hostname.
---

Onboard an application repository onto LEco DevOps.

Target: **$ARGUMENTS** (first argument is the app path, second — optional — is the app id).

Read the `leco:operate` skill and its `references/onboarding.md` first.

## 1. Resolve the path

App paths use a rooted form, not absolute filesystem paths. Call `leco_browse(root="wsp")`
and use the returned `path_field` **verbatim** (`wsp:MyApp`). `root="project"` is for paths
inside the `local-ecosystem` checkout itself. Do not construct `wsp:` strings by guessing.

## 2. Agree the slug before writing anything

The `app_id` becomes the hostname `<app_id>.lh`, the compose project name, and the registry
id. Changing it later means re-merging Traefik and possibly remapping stale Docker DNS hosts
in the manifest. If the user did not specify one, propose the detected default and confirm.

## 3. Detect — and actually read the result

`leco_detect(path, app_id)` writes nothing. Check:

- **`main_url_warnings`** — if there is no compose signal **and** no wrangler signal, the app
  has no infrastructure to route to. Onboarding will report success and produce a hostname
  that never answers. **Say so and stop.** Do not onboard hopefully.
- **`archetype`** — drives the defaults.
- **Published host ports** — these will collide with LEco core (Traefik owns 80/443). The
  generated overlay applies `ports: !reset []`; know what is being reset.
- **The previewed manifest** — if the previewed routing does not describe what the app
  actually serves, take the step-by-step path in step 4b.

## 4a. Fast path (detect came back clean)

`leco_onboard(path, app_id, deploy=True)` — detect, generate manifest, register, merge
Traefik routes, deploy. Each stage is reported separately, so a failure is attributable to
one of them. Read the stage results; do not just check for overall success.
`regenerate_manifest=False` reuses manifests already on disk.

## 4b. Hand-tuned path (custom routes, ports, split frontend/API, or a local edge runtime)

`leco_manifest_status` → `leco_manifest_generate` → `leco_manifest_read` → edit →
`leco_manifest_validate` → `leco_manifest_save` → `leco_register(path, app_id, deploy=True)`.

`leco_manifest_generate` **overwrites** existing LEco manifests — check status first if the
repo may carry hand edits. `leco_manifest_samples()` often gives a better starting point than
editing a generated manifest. `leco_manifest_urls(localhost_yaml, set_urls=…)` rewrites just
the public-URL rows without touching the rest.

Never edit the upstream repo. LEco-only concerns go into overlay compose files under
`hosting/app-available/<slug>/`, referenced via
`infrastructure.dockerCompose.additionalComposeFilesFromManifest`.

## 5. Verify — onboarding is not done when the tool returns success

`leco_app_snapshot(slug, sections=["runtime","urls"])`. A fresh deploy can take a moment to
answer; one retry is reasonable, a loop is not. If a URL fails:

- **404** → no route. The Traefik merge did not happen, or the manifest declares no routing
  (register logs "Traefik merge skipped" and still exits successfully — the most common
  silent failure). Fix with `leco_register(..., deploy=False)` or
  `leco_route_fragment_from_app` + `leco_route_merge_fragment`. See `/leco:routes`.
- **502** → route exists, backend unreachable. Usually the containers are not on
  `lh-network`. Run `leco_app_validate(slug)` — it heals the hosting overlay — then
  `leco_app_control(slug, "recreate")`. See `/leco:validate`.

## 6. Report

The main URL, the container state, every fix you had to apply, and anything left unresolved.

Reversing an onboarding is `/leco:offboard`, and it is destructive.

## Without the MCP server

Say so first, then `./leco-cli.sh apps onboard <path>` (path to `leco.app.yaml` or its
directory), or `leco-devops init` / `leco-devops onboard` from inside the app directory with
`LECO_ECOSYSTEM_ROOT` exported. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs.
