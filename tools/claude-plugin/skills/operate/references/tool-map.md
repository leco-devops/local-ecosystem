# Tool map — which of the 60 tools to reach for

Every tool is prefixed `leco_`. All of them are HTTP calls to the LEco DevOps dashboard API;
none touch Docker directly. Tool descriptions are authoritative for parameters — this file
is about **choosing** and **sequencing**, and about what things cost.

---

## Cost model (read this first)

| Cheap, call freely | Expensive — narrow before calling |
|--------------------|-----------------------------------|
| `leco_server_info`, `leco_version`, `leco_services`, `leco_control_targets`, `leco_apps`, `leco_dev_stacks`, `leco_traefik_routes` | `leco_status(detail="full")` — the entire overview payload, ~56 KB |
| `leco_urls(only_unhealthy=True)` | `leco_status()` at all — collects **live Docker stats**, 10–15 s |
| `leco_app_snapshot` with explicit `sections` | `leco_app_snapshot(full=True)` |
| `leco_logs(container, level="error", search=...)` | `leco_logs` with a big `tail_lines` and no filter |

Responses are truncated at `LECO_MCP_MAX_RESPONSE_CHARS` (default 60 000). If you hit the
cap, the answer is a narrower filter, not a bigger cap.

Default to the cheapest thing that answers the question. `leco_urls(only_unhealthy=True)` is
usually a better first diagnostic than `leco_status()`.

---

## Observability

| Tool | Use it for |
|------|-----------|
| `leco_server_info` | **First call when anything fails wholesale.** Resolved dashboard URL, token configured?, destructive/credential gates on? |
| `leco_status(detail=)` | `summary` (default) → health headline. `services` → per-service rows with URL checks. `full` → raw payload, avoid. |
| `leco_services` | Map a friendly service name → Docker container name before calling `leco_logs` or `leco_control` |
| `leco_logs(container, …)` | Container logs with server-side `search` and `level` filtering. Returns flattened text, not JSON scaffolding |
| `leco_urls(only_unhealthy=True)` | Fastest "what is broken" after a deploy |
| `leco_metrics(limit)` | CPU/RAM/health time series. Each call also appends a sample |
| `leco_traefik_routes(hostname=)` | Routers/services Traefik has **loaded**, with hosted-app ownership hints |
| `leco_cloudflare_local` | Health of the R2/KV/D1/Workers/browser adapters |
| `leco_version` | Platform / CLI / update-catalog versions |

---

## Control (turning infrastructure on and off)

`leco_control_targets(group=, running=, query=)` → `leco_control(target_id, action)`.

Never guess a `target_id`. Discover it. Groups: `ecosystem`, `ecosystem-stack`, `infra`,
`cloudflare-local`, plus `leco-stack-<slug>` for hosted apps.

Actions: `start stop restart pause unpause deploy recreate backup staging` are ordinary.
`remove` (delete container) and `reset` (delete container **and its data volume**) are
destructive — `confirm=True` **and** `LECO_MCP_ALLOW_DESTRUCTIVE=1`.

`stream=True` (default) streams compose output live and returns the tail. Keep it on for
anything that can hang; you get partial progress instead of a timeout with no information.

Bulk targets: `stack-ecosystem-all`, `stack-infra-all`, `stack-file-transfer-all`,
`stack-cf-all`. Prefer the bulk target for a cold start — it sequences dependencies
internally. Sequence by hand only when recovering a specific failure.

`leco_control_policies()` reads per-target start policy (`start` / `stop` / `offloaded`).
A target that "won't come up with the stack" is usually policy-`offloaded`, not broken.
Pass `set_policies={...}` to change (merged, not replaced).

---

## Hosted apps

| Tool | Use it for |
|------|-----------|
| `leco_apps(running=, query=)` | Inventory, incl. materialized-but-unregistered apps (`pending_registration=true`) |
| `leco_app_snapshot(slug, sections=[…])` | Deep detail. Sections: `identity`, `runtime`, `urls`, `manifest`, `services`, `data`. Default is identity+runtime+urls — keep it that way |
| `leco_app_control(slug, action, confirm=)` | Per-app lifecycle. `deploy`/`stop` go through `leco-devops`; `restart`/`recreate`/`pause` are plain compose. `remove` also offboards; `reset` also deletes volumes |
| `leco_app_logs(slug, service=, search=)` | Compose logs; `service=""` covers every compose service |
| `leco_app_insights(slug)` | Detected problems — restart loops, error spikes, routing gaps. Cheap; call it before reading raw logs |
| `leco_app_validate(slug)` | Manifest + profile vs schema and on-disk paths. **Run this when an app deploys but does not route.** It also auto-heals the `lh-network` hosting overlay |
| `leco_app_metrics(slug, limit)` | Per-app CPU/mem/network series |
| `leco_app_bind_dev_stack(slug, dev_stack_id)` | Writes `platform.devStackId`. **Redeploy afterwards** or nothing changes. `dev_stack_id=""` unbinds |
| `leco_app_data_import_plan(slug)` → `leco_app_data_import(slug, dry_run=…)` | Seed data. `dry_run=True` is the default and should stay that way until the plan has been reviewed with the user |
| `leco_app_offboard(slug, confirm=True)` | Unregister + strip routes + drop registry row. Keeps containers |

---

## Onboarding

`leco_browse` → `leco_detect` → `leco_onboard` is the fast path.
`leco_manifest_*` + `leco_register` is the hand-tuned path. See `onboarding.md`.

| Tool | Note |
|------|------|
| `leco_browse(root="wsp"\|"project", path=)` | Use the returned `path_field` verbatim |
| `leco_detect(path, app_id, full=)` | Writes nothing. **Read `main_url_warnings`** |
| `leco_manifest_status(path, app_id)` | Check before generating — generate overwrites |
| `leco_manifest_generate(path, app_id)` | Overwrites existing LEco manifests |
| `leco_manifest_read` / `leco_manifest_validate` / `leco_manifest_save` | Edit loop. Invalid YAML is rejected, not written |
| `leco_manifest_urls(localhost_yaml, set_urls=)` | Rewrite just the public-URL rows |
| `leco_manifest_samples(name=)` | Preset manifest/profile pairs — usually a better starting point than editing a generated one |
| `leco_register(path, app_id, deploy=True)` | Requires manifests on disk. Writes the registry, merges Traefik, deploys. Streams |
| `leco_onboard(path, app_id, …)` | All of the above in one call, stage by stage. `regenerate_manifest=False` reuses on-disk manifests |

---

## Platform and dev stacks

| Tool | Note |
|------|------|
| `leco_platform_config(set_config=)` | Reading is safe. Writing **replaces the whole file** — read, merge, then write. Changing `base_domain` or TLS mode affects every route on the machine |
| `leco_platform_catalog(section=)` | `presets` lists the one-click dev stacks for `leco_dev_stack_create` |
| `leco_platform_services` / `leco_platform_service_action(id, action)` | `install`/`disable` toggle enablement in `leco-platform.yaml`; `start`/`stop` only touch the container. For per-container lifecycle machine-wide, use `leco_control` |
| `leco_platform_traefik_apply()` | Regenerate + apply platform Traefik routes. **The fix for a global 404 from a stale `hosting/traefik/01-stack-core.yml`** |
| `leco_dev_stacks` / `leco_dev_stack_snapshot(id)` / `leco_dev_stack_access(id)` | `access` returns credentials — treat as sensitive |
| `leco_dev_stack_create(id, preset\|template\|components, sample_data=)` | Exactly one of preset / template / components. Creating does **not** start it |
| `leco_dev_stack_action(id, action, confirm=)` | `start stop repair redeploy` are safe. `repair` fixes routing and network attachment in place, keeping data and manual edits — **try it first**. `reinstall` and `destroy` are destructive |
| `leco_dev_stack_files(id, path=, write_content=)` | List / read / write generated config. Redeploy for changes to apply; `reinstall` reverts them |
| `leco_dev_stack_reset_admin(id)` | New template admin credentials (WordPress, Magento, …) |

---

## Routing

| Tool | Note |
|------|------|
| `leco_route_fragment_from_app(slug)` | Read-only. The routes the manifest **implies** — diff against `leco_traefik_routes`, which is what Traefik **has** |
| `leco_route_merge_fragment(yaml_fragment)` | Additive merge into `hosting/traefik/dynamic.yml`. Same-named keys are replaced, everything else kept. Traefik hot-reloads |
| `leco_route_strip_keys(routers=, services=, confirm=True)` | Destructive — takes an app offline at its hostname. Normal teardown goes through `leco_app_offboard`, which strips routes for you |

---

## Local models

| Tool | Note |
|------|------|
| `leco_llm_models(runtime=, installed_only=)` | What is **installed** and what is resident in memory |
| `leco_llm_catalog(runtime=, query=, limit=)` | What you **could** install — the tracked upstream catalog. Different question |
| `leco_llm_model_action(action, model, runtime, confirm=)` | `pull` starts in the background — poll `leco_llm_models`, do not block. `unload` evicts from memory. `delete` is destructive |
| `leco_llm_model_inspect(model, runtime)` | Manifest, layers, template, parameters |

---

## Knowledge

Reach for these **before** grepping the repo — they are the same content the dashboard
serves, already indexed.

| Tool | Note |
|------|------|
| `leco_docs(doc_id=, category=)` | Architecture/operations docs. No args → the list |
| `leco_help(topic_id=, search=)` | Operator/developer manuals. `search="502"` does full-text search across every help page |
| `leco_updates(unread_only=)` / `leco_updates_mark_read()` | Tracked ecosystem/stack/image updates |

---

## Credentials

`leco_ui_credentials`, `leco_ui_credentials_set`, `leco_ui_credentials_reset`.

Disabled unless the server was started with `LECO_MCP_ALLOW_CREDENTIALS=1`. They return
**plaintext local-dev secrets**. Never echo them into logs, commits, a commit message, a PR
body, or anything that leaves this machine. `leco_ui_credentials_set` on a protocol service
(SFTP/FTP) rewrites `file-transfer/.env` and recreates the container — **active sessions
drop**. `leco_ui_credentials_reset` additionally needs `confirm=True` and
`LECO_MCP_ALLOW_DESTRUCTIVE=1`.

---

## The safety model, stated once

Two independent gates protect anything that destroys data:

1. `confirm=True` on the call — so an agent cannot wipe a volume as a side effect of a vague
   instruction; and
2. the server was started with `LECO_MCP_ALLOW_DESTRUCTIVE=1`.

Gated actions: `remove`, `reset`, dev-stack `destroy` / `reinstall`, `leco_app_offboard`,
`leco_route_strip_keys`, `leco_llm_model_action(action="delete")`,
`leco_ui_credentials_reset`.

If a call comes back `Blocked destructive action`, **report it to the user with the reason
and stop.** Do not route around it with `leco_control` on a different target, do not shell
out to `docker`, do not edit files to achieve the same effect. The block is the product
working correctly.
