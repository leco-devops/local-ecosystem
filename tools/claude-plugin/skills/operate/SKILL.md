---
name: operate
description: Operate the LEco DevOps local platform - Traefik routing on *.lh, Docker Compose stack lifecycle, hosted-app onboarding, isolated dev stacks, Cloudflare-local adapters, and local LLM runtimes. Use when the user asks to bring the stack up or down, check stack or service health, onboard/register/deploy an app repo onto LEco, debug a 502 or 404 on a *.lh hostname, fix Traefik routes or lh-network, work with dev stacks, manage Ollama/AirLLM models, or when working in the local-ecosystem repository. Covers both the leco-devops MCP tools and the leco-cli.sh / ecosystem-stack.sh / leco-devops CLI fallback.
---

# Operating LEco DevOps

LEco DevOps is a local cloud edge: Traefik terminating `*.lh` on ports 80/443, Docker
Compose stacks orchestrated behind it, isolated dev stacks, Cloudflare-local adapters, local
LLM runtimes, and one-click onboarding of application repos.

Every action goes through the **LEco DevOps dashboard API**, which is the single source of
truth. The `leco_*` MCP tools and the dashboard UI do exactly the same things — what you
change is visible in the UI immediately, and there is no second state to reconcile.

## Before anything else

1. **`leco_server_info`** — is the dashboard reachable, is a control token configured, are
   the destructive and credential gates open? Nearly every "the tools are broken" report is
   answered here. If the tools do not exist at all, read `references/cli-fallback.md`.
2. **Establish which failure class you have before you act.** Almost every LEco incident is
   one of two things, and they have opposite fixes:
   - **404 → there is no route.** The Traefik merge never happened, or the manifest has no
     routing at all, or the whole file provider is broken.
   - **502 → the route exists and the backend is unreachable.** Almost always the container
     is not on `lh-network`, or Traefik's upstream name does not match Docker DNS.

   `leco_traefik_routes(hostname="<host>")` distinguishes them in one call.

## Non-negotiable naming

Application / UI / CLI product name: **LEco DevOps**. Project / repository brand: **LEco
DevOps Open Project**. Published CLI entrypoint: **`leco-devops`** (Python import path stays
`leco_app`). Use these exactly in anything you write or say.

---

## Core operational rules

**Diagnose, then act. Never redeploy hopefully.** A failed URL probe is information, not a
reason to run `deploy` again. `leco_app_validate` and `leco_app_insights` are cheap and tell
you what a redeploy would not.

**Respect dependency order.** Traefik first — nothing routes until the edge is up. Then
`ai-postgres` before `ai-n8n`, and `ai-paperclip-postgres` before `ai-paperclip`. For a cold
start prefer the bulk target `stack-ecosystem-all`, which sequences internally; sequence by
hand only when recovering a specific failure.

**Discover ids, never guess them.** `leco_control_targets` for control ids, `leco_apps` for
slugs, `leco_services` for container names, `leco_browse` for app paths. A guessed
`target_id` fails; a guessed *wrong-but-valid* one acts on the wrong service.

**Budget your calls.** `leco_status()` collects live Docker stats and takes 10–15 seconds;
`detail="full"` returns ~56 KB. `leco_urls(only_unhealthy=True)` usually answers "what is
broken" faster and smaller. Ask `leco_app_snapshot` for explicit `sections` rather than
`full=True`. See the cost table in `references/tool-map.md`.

**Never modify the upstream app repo.** LEco-only concerns — `lh-network` membership,
`ports: !reset []`, `*.lh` env defaults, local edge runtimes — live in overlay compose files
under `hosting/app-available/<slug>/`, referenced through
`infrastructure.dockerCompose.additionalComposeFilesFromManifest`. If you find yourself
about to edit someone's `docker-compose.yml`, you are solving it in the wrong place.

**Stop at a safety gate; do not route around it.** `remove`, `reset`, dev-stack `destroy`
and `reinstall`, `leco_app_offboard`, `leco_route_strip_keys`, model `delete`, and credential
reset all require `confirm=true` **and** a server started with
`LECO_MCP_ALLOW_DESTRUCTIVE=1`. Credential tools additionally need
`LECO_MCP_ALLOW_CREDENTIALS=1`. If a call returns `Blocked destructive action`, report the
block and its reason to the user and stop. Do not achieve the same effect with a shell
command, a different target, or a file edit.

**Treat credentials as radioactive.** `leco_ui_credentials` and `leco_dev_stack_access`
return plaintext local-dev secrets. Never put them in logs, commits, PR bodies, or anything
that leaves the machine.

**Reach for the platform's own docs first.** `leco_help(search="502")` does full-text search
across every operator and developer manual, and `leco_docs()` lists the architecture docs.
Both are faster and more current than grepping the repository.

---

## Common workflows

### Bring the stack up

`leco_control_targets(running="no")` → see what is down →
`leco_control("stack-ecosystem-all", "start")` (or per-target, Traefik first) →
`leco_status()` until healthy → `leco_urls(only_unhealthy=True)`.
Report what started, what stayed down, and why. A target that refuses to start with the
stack is often policy-`offloaded`, not broken — check `leco_control_policies()`.

### Diagnose a degraded stack

`leco_status(detail="services")` → `leco_urls(only_unhealthy=True)` →
`leco_logs(container, level="error")` for each failure. Global 404 across every hostname
means the Traefik file provider is broken → `leco_platform_traefik_apply()`. A single app
502 means `lh-network` → `leco_app_validate(slug)`. Do not run destructive actions while
diagnosing.

### Onboard an app repo

`leco_browse(root="wsp")` → `leco_detect(path)` → **read `main_url_warnings`** → `leco_onboard(path, app_id)`
→ verify with `leco_app_snapshot(slug, sections=["runtime","urls"])`.

If detect reports no compose signal and no wrangler signal, the app has no infrastructure to
route to. Say so and stop — onboarding will "succeed" and produce a hostname that never
answers. If the app needs hand-tuned routes or ports, use the `leco_manifest_*` +
`leco_register` path instead. Agree the `app_id` with the user first: it becomes the
hostname, the compose project name, and the registry id, and changing it later is expensive.

Full detail: `references/onboarding.md`.

### Fix a broken route

`leco_route_fragment_from_app(slug)` gives the routes the manifest **implies**;
`leco_traefik_routes(hostname=...)` gives the routes Traefik **has**. Diff them. If the
manifest is right and Traefik is stale, `leco_route_merge_fragment(fragment)` — the merge is
additive and Traefik hot-reloads. If the manifest is wrong, fix the manifest and re-register;
do not paper over it with a hand-written fragment that will be overwritten on next register.

### Work with dev stacks

`repair` first — it fixes images, routing, and `lh-network` attachment in place and keeps
volumes and manual edits. Escalate to `reinstall` only when the template config itself is
wrong (it wipes data and reverts manual edits), and to `destroy` only when the stack should
cease to exist. Binding an app with `leco_app_bind_dev_stack` requires a redeploy to take
effect.

---

## The `/leco:*` command set

Each of these is a skill in this plugin carrying the order of operations for one workflow.
Invoke the matching one rather than improvising the sequence.

`/leco:status` · `/leco:up` · `/leco:diagnose` · `/leco:urls` · `/leco:logs` — observe and
recover.
`/leco:apps` · `/leco:deploy` · `/leco:validate` · `/leco:onboard` · `/leco:offboard` —
hosted apps, first to last.
`/leco:routes` · `/leco:platform` · `/leco:cf-local` — edge routing, platform services and
config, Cloudflare-local adapters.
`/leco:dev-stack` · `/leco:models` · `/leco:credentials` — isolated dev stacks, local LLM
runtimes, the UI credential vault.
`/leco:docs` · `/leco:mcp-server` — the platform's own documentation, and fixing the tool
connection itself.

`/leco:offboard`, and `destroy` / `reinstall` within `/leco:dev-stack`, delete data and are
gated. `leco-diagnostician` is a read-only subagent for investigations that will burn many
tool calls.

## Reference files

Load these on demand; do not read them all up front.

| File | Read it when |
|------|--------------|
| `references/architecture.md` | You need to know why something lives where it does — Traefik file layout, hosted apps vs dev stacks, control-target groups, Cloudflare-local, the URL-probe quirk |
| `references/troubleshooting.md` | Something is broken. 502, global 404, port collisions, SPA calling localhost, Worker 404s and D1 errors, Varnish 503, crash loops, `unauthorized` |
| `references/onboarding.md` | Onboarding or re-onboarding an app: `wsp:` paths, manifest v3 bridge/profile split, compose overlay fields, routing shapes, offboarding |
| `references/tool-map.md` | Choosing between tools, or you need the cost model and the full safety-gate list |
| `references/cli-fallback.md` | The MCP tools are unavailable. `leco-cli.sh`, `ecosystem-stack.sh`, `leco-devops`, and direct dashboard API calls |

---

## When the MCP server is not available

Say so, then fall back to the shell — `./leco-cli.sh diagnose`, `./leco-cli.sh stack …`,
`./leco-cli.sh apps …`, `./ecosystem-stack/ecosystem-stack.sh heal traefik`, and
`leco-devops` from inside the app directory. Read `references/cli-fallback.md` for the
mapping.

Two cautions. `./leco-cli.sh` with no arguments opens an interactive menu and will hang —
always pass a subcommand. And the shell has **no confirmation gate**: `stack reset`,
`apps unregister`, and `dev-stack destroy` delete data immediately. In fallback mode you are
the safety layer, so get explicit user agreement before running any of them.

Often the real fix is simply `pip install -e tools/mcp-server` followed by `leco-mcp doctor`.
