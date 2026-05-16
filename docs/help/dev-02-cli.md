# CLI & schema (`leco-devops`)

Package: `tools/deploy-cli/` · import path: `leco_app` · command: **`leco-devops`**.

Install: `cd tools/deploy-cli && pip install -e .`

## Entry & commands

**`cli.py`** — Typer application. Important commands:

| Command | Purpose |
|---------|---------|
| `detect` | Scan directory → JSON (wizard/CI) |
| `init` | Interactive manifest stub |
| `scaffold` | Copy sample → `hosting/app-available/<slug>` (includes `data/` README template) |
| `import-data` | Import `data/` dumps into running compose / local CF (`--dry-run`, `--reimport`) |
| `deploy` | `docker compose up -d --build` |
| `onboard` | deploy + register + traefik |
| `ecosystem-register` | Registry + optional CF provision + Traefik |
| `ecosystem-unregister` | Teardown + hosting dir removal |
| `offload` | Staging: down + strip Traefik |
| `provision-local-cf` | KV/R2/D1 from wrangler |
| `run-hooks` | lifecycle phases |
| `traefik-fragment` | Emit YAML preview |
| `runtimes` | Runtime diagnostic (`--detect`) |
| `stop` / `down` / `status` / `logs` | Compose lifecycle |

Env: **`LECO_ECOSYSTEM_ROOT`** required for register/unregister/onboard.

## Schema (`schema.py`)

| Model | File on disk |
|-------|----------------|
| `ApplicationManifest` | `leco.app.yaml` (bridge) |
| `LocalhostProfile` | `leco.yaml` (profile) |
| `ProfileInfrastructureSpec` | `infrastructure` block |
| `DockerComposeSpec` | compose paths, manifest-relative files |
| `RoutingEntry`, `RoutingUpstreamRule` | Traefik generation |
| `LocalRuntimeSpec` | edge runtime overlay |

**`load_effective_manifest(path)`** — merges profile `infrastructure` onto bridge. Used by CLI and dashboard (`leco_validate.py`, `leco_registration.py`).

## Compose execution

**`compose_runner.py`**

- `compose_args(manifest_path)` — builds `-f` list:
  - `composeFileFromManifest` replaces primary when set
  - `additionalComposeFiles` from resolved root
  - `additionalComposeFilesFromManifest` from manifest directory
- `run_compose()` — subprocess `docker compose`

## Registry

**`ecosystem_registry.py`**

- `register_in_ecosystem()` — append `config/leco-registry.yaml`
- `unregister_from_ecosystem()` — CF teardown hook, compose down, delete `hosting/app-available/<slug>` when manifest under `hosting/`

## Onboarding pipeline

**`onboarding.py`**

- `run_registry_and_provision()`
- `run_traefik_merge_for_manifest()` → `traefik_dynamic_merge.py`

## Traefik generation

| Module | Role |
|--------|------|
| `traefik_fragment.py` | `routing_entry_fragment`, `manifest_to_traefik_yaml` |
| `traefik_dynamic_merge.py` | Atomic upsert into `hosting/traefik/dynamic.yml` |
| `traefik_dynamic_cleanup.py` | Strip keys on offload |
| `traefik_dynamic_sanitize.py` | Prune invalid empty `http: {}` |

## Detectors

`detectors/compose.py`, `wrangler.py`, `ports.py`, `archetype.py` — used by `cmd_detect` and inform dashboard defaults (keep aligned with `leco_detect.py`).

## Local Cloudflare

`local_cf_provision.py`, `local_cf_policy.py`, `local_cf_teardown.py` — shared adapters, not per-app containers.

## Adding a manifest field (checklist)

1. Add to Pydantic models in **`schema.py`**
2. Update **`leco_validate.py`** if dashboard validates separately
3. Update **`leco_detect.py`** defaults / `ensure_*` if auto-generated
4. Update **`compose_runner.py`** or **`traefik_fragment.py`** if behavior changes
5. Document in **`docs/LECO_APP_BLUEPRINT.md`**
6. `python3 -m compileall -q dashboard tools/deploy-cli/leco_app`

Next: [Registration data flow](help:dev-registration-flow)
