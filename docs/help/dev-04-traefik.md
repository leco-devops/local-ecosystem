# Traefik & routing (developer)

Traefik uses a **file provider** watching **`hosting/traefik/`**. Two files matter at runtime:

| File | Source | Contents |
|------|--------|----------|
| `01-stack-core.yml` | Copied from `traefik/dynamic.yml` on Traefik start | Platform routes: `localhost.lh`, `ollama.lh`, `airllm.lh`, `n8n.lh`, CF adapters, … |
| `dynamic.yml` | Merged by `leco-devops` / dashboard | Per-app routers from `routing.entries` |

**Do not** symlink `01-stack-core.yml` — Traefik fsnotify needs a real file copy (`ecosystem-stack/services/traefik.sh` `ensure_hosting_files`).

## Merge implementation

1. **`traefik_fragment.py`** — `routing_entry_fragment(entry, slug)` builds router + service YAML from:
   - Modern `upstream[]` (`target: service` | `target: runtime`)
   - Legacy `frontend` / `apiBackend` / `backendHost`+`backendPort`
2. **`traefik_dynamic_merge.py`** — `merge_manifest_routing_into_dynamic_yml()` upserts keys, atomic write
3. **`onboarding.run_traefik_merge_for_manifest`** — called during register

Dashboard **`traefik_dynamic_file.py`** uses same paths for Routes tab API.

## Invalid YAML guard

Empty `http: {}` breaks Traefik. Scripts:

- `ecosystem-stack/scripts/normalize-hosting-traefik-dynamic.py`
- `traefik_dynamic_sanitize.py`

Run **`heal traefik`** after manual edits or failed merges.

## lh-network contract

Backend services must be reachable on Docker network **`lh-network`**. Hosting overlay adds network membership; without it → **502**.

Service URL in Traefik: `http://<container_name>:<port>` on `lh-network`.

## Dashboard URL probes

**`hosted_apps.py`** + **`monitor.py`** — health checks from `infrastructure.healthcheckUrls` and `urls[]`. Disable: `DASHBOARD_HOSTED_APP_HEALTH_PROBES=0`.

## Changing stack routes

1. Edit **`traefik/dynamic.yml`** in git
2. `./ecosystem-stack/ecosystem-stack.sh heal traefik` or restart `traefik` service
3. Verify `hosting/traefik/01-stack-core.yml` updated

## Changing app routes

1. Edit `leco.yaml` → `infrastructure.routing.entries`
2. Re-register with `--merge-traefik` or dashboard **Register**
3. Inspect `hosting/traefik/dynamic.yml`

Operator runbook: **`docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md`**.

Next: [Ecosystem stack](help:dev-ecosystem-stack)
