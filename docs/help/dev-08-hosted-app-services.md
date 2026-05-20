# Attached services — developer reference

Operator-facing guide: [Attached services panel](help:hosted-app-attached-services).

## Module

| File | Role |
|------|------|
| `dashboard/hosted_app_services.py` | Compose merge, service classification, credentials, `connection_endpoints`, management UIs, `build_attached_services()` |
| `dashboard/hosted_apps.py` | Calls `build_attached_services()` in `snapshot_for_slug()` |
| `dashboard/static/dashboard.js` | `renderHostedAttachedServices()` — labeled connection rows, copy buttons, Compass link |
| `dashboard/tests/test_hosted_app_services.py` | Classification, URI builders, enrich |

**Compose parity:** `load_merged_compose_services()` and `compose_paths_from_tail()` must match `leco-devops deploy` (`compose_tail` from `leco_control.py`). `leco_detect.load_compose_services_for_detect()` imports the same loader.

## Snapshot API

`GET /api/hosted-apps/<slug>/snapshot` includes:

```json
{
  "attached_services": {
    "generated_at": "2026-05-16T12:00:00+00:00",
    "local_dev_only": true,
    "groups": [
      {
        "id": "data_stores",
        "label": "Data stores",
        "items": [ { "...": "..." } ]
      }
    ]
  }
}
```

### Data-store item shape

| Field | Type | Notes |
|-------|------|--------|
| `id` | string | e.g. `compose-mongo` |
| `name` | string | Compose service name |
| `kind` | string | `mysql`, `postgres`, `redis`, `mongodb`, `minio`, … |
| `source` | string | `compose` or `ecosystem` |
| `status` | string | From `docker compose ps` when available |
| `credentials` | object | Plain key/value (no `connection_string` after enrich) |
| `connection_endpoints` | array | **Preferred** — `{ "scope", "label", "uri" }` |
| `connection_strings` | array | Flat URIs (backward compat; order matches endpoints) |
| `management_uis` | array | `{ "label", "url" }` — host-safe links for Compass / Adminer |
| `hub_slug`, `login_url`, `can_auto_login` | | Ecosystem vault integration |
| `notes` | string | Ports, image, optional hints |

### `connection_endpoints` scopes

| `scope` | Default label | Meaning |
|---------|---------------|---------|
| `host` | From your Mac (host) | `127.0.0.1:<published-port>` |
| `host_lh` | From your Mac (*.lh DNS) | `mysql.lh`, `postgres.lh`, `redis.lh`, `s3.lh`, … |
| `docker` | From app containers (Docker DNS) | Compose service name as hostname |

Built by `_build_connection_endpoints()`; hints from `_collect_connection_hints_from_compose()` (app service env). Docker URIs from env are rewritten to host when `_host_port_from_publish()` finds a mapping (`_data_uri_for_host()`).

**Management UIs:** `_management_uis_for_data_store()` — MongoDB Compass uses `_build_host_mongodb_uri()` only (never `mongodb://mongo:…`).

## Key functions

| Function | Purpose |
|----------|---------|
| `classify_compose_service(name, spec)` | Image/name → kind (`mysql`, `edge-runtime`, …) |
| `_extract_credentials(kind, env, service_name)` | Env → credentials + default docker `connection_string` |
| `_build_connection_endpoints(...)` | Host + docker + `*.lh` rows per kind |
| `_enrich_data_store_items(...)` | Attach endpoints to compose data-store cards |
| `_compose_items(...)` | All compose services → items; enriches data stores |
| `build_attached_services(manifest_path, …)` | Grouped payload for snapshot |

## When to update (checklist)

Changing attached-services behavior usually touches:

1. `dashboard/hosted_app_services.py`
2. `dashboard/static/dashboard.js` + `dashboard.css` (if UI shape changes)
3. `dashboard/tests/test_hosted_app_services.py`
4. `docs/help/12-hosted-app-attached-services.md` (operators)
5. This file + `dashboard/help_manual.py` TOC
6. `CHANGELOG.md` `[Unreleased]`
7. If compose file resolution changes: `dashboard/leco_detect.py`, `tools/deploy-cli/leco_app/compose_runner.py`, `AGENTS.md` sync list

## Tests

```bash
python3 -m unittest dashboard.tests.test_hosted_app_services -v
```

## Related code paths

- Ecosystem hub rows: `_ecosystem_hub_item()` + `monitor.SERVICE_MAP`
- CF bindings: `_cf_items()` from manifest UI preview
- Wrangler hyperdrive → postgres hub row in `build_attached_services` tail

Next: [Dashboard architecture](help:dev-dashboard) · [Extending LEco](help:dev-extending)
