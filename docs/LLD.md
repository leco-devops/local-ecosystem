# LEco DevOps Open Project - LLD

This Low-Level Design (LLD) maps concrete modules, APIs, and responsibilities.

## 1) Dashboard backend module map

| Module | Responsibility |
| ----- | ----- |
| `dashboard/app.py` | Flask entrypoint and API routing (`/api/*`, docs, hosted and LEco endpoints) |
| `dashboard/control.py` | Control action validation/execution; stack and hosted action orchestration |
| `dashboard/control_targets.py` | Static target inventory for ecosystem stack, Cloudflare-local, infra |
| `dashboard/leco_subprocess.py` | Runs LEco CLI commands from dashboard runtime |
| `dashboard/leco_registration.py` | Register/stream register flow wrappers |
| `dashboard/leco_detect.py` | App scanning and YAML generation helpers |
| `dashboard/leco_materialize.py` | Writable materialization for read-only roots |
| `dashboard/hosting_layout.py` | Source/target path policy and symlink handling |
| `dashboard/hosted_apps.py` | Registry-based hosted listing, snapshots, manifest-driven UI fields |
| `dashboard/hosted_offboard.py` | Offboard helper around unregister flow |
| `dashboard/docs_catalog.py` | Whitelisted docs surfaced in in-app Docs tab |
| `dashboard/monitor.py` | Service map, metrics aggregation, probes, and overview payloads |

## 2) LEco CLI module map

| Module | Responsibility |
| ----- | ----- |
| `tools/deploy-cli/leco_app/cli.py` | Typer CLI commands and operator UX |
| `tools/deploy-cli/leco_app/schema.py` | Manifest/profile schema and effective merge logic |
| `tools/deploy-cli/leco_app/ecosystem_registry.py` | Registry CRUD and unregister behavior |
| `tools/deploy-cli/leco_app/compose_runner.py` | Compose command orchestration and path handling |
| `tools/deploy-cli/leco_app/traefik_io.py` | Route fragment generation and dynamic file merge/strip |
| `tools/deploy-cli/leco_app/local_cf_*` | Local CF provisioning and teardown routines |

## 3) Main runtime APIs

### Control and observability

- `GET /api/overview`
- `GET /api/metrics/history`
- `GET /api/control/targets`
- `POST /api/control`
- `POST /api/control/stream`

### LEco hosted workflows

- `GET /api/hosted-apps`
- `GET /api/hosted-apps/<slug>/snapshot`
- `GET /api/hosted-apps/<slug>/insights`
- `POST /api/hosted/upload-zip`
- `POST /api/leco/browse`
- `POST /api/leco/detect`
- `POST /api/leco/yaml-status`
- `POST /api/leco/generate-yaml`
- `POST /api/leco/save-yaml`
- `POST /api/leco/register`
- `POST /api/leco/register/stream`

### Docs

- `GET /api/docs/catalog`
- `GET /api/docs/content?id=<doc-id>`

## 4) Data/config contracts

- Registry: `config/leco-registry.yaml` (runtime) and `config/leco-registry.example.yaml`.
- Hosted materialization root: `hosting/app-available/<slug>/`.
- Traefik dynamic routes: `traefik/dynamic.yml`.
- App manifests:
  - Bridge: `leco.app.yaml`
  - Profile: `leco.yaml` (or referenced local profile variant)

## 5) Execution sequence (register)

```mermaid
sequenceDiagram
  participant U as User
  participant UI as LEcoDevOpsUI
  participant API as FlaskAPI
  participant MAT as MaterializeLayer
  participant CLI as leco-app
  participant REG as RegistryYaml
  participant TR as TraefikDynamic

  U->>UI: Detect + generate/save
  UI->>API: POST /api/leco/detect
  API->>MAT: resolve/mirror paths
  U->>UI: Register
  UI->>API: POST /api/leco/register
  API->>CLI: ecosystem-register
  CLI->>REG: upsert app row
  CLI->>TR: merge/validate routes (if configured)
  API-->>UI: result + logs
```

## 6) Operational guardrails

- Prefer token-gated control in shared environments (`DASHBOARD_CONTROL_TOKEN`).
- Keep CLI and dashboard semantics aligned through schema/effective-manifest logic.
- Avoid direct/manual registry or route mutation when equivalent CLI/API exists.
