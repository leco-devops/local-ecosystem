# Platform cloud APIs (developer)

Cloud VM platform features expose dashboard APIs and shell helpers.

## Config files

| File | Role |
|------|------|
| `config/leco-platform.yaml` | Deployment mode, domain, TLS, enabled services, dev stack registry |
| `ecosystem-stack/config/install-profiles.yaml` | Profile definitions |
| `ecosystem-stack/config/component-catalog.yaml` | Dev stack component versions |
| `ecosystem-stack/config/dev-stack-presets.yaml` | Quick presets (infrastructure, CMS, frameworks) |
| `ecosystem-stack/lib/platform_config.py` | Profile resolution, `enabled-services` CLI |

## REST APIs — platform

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/api/platform/config` | Read/write platform YAML (POST needs control token) |
| GET | `/api/platform/catalog` | Component catalog for dev stack builder |
| GET | `/api/platform/services` | Ecosystem service status |
| POST | `/api/platform/services/<id>/action` | `start` / `stop` (control token) |
| POST | `/api/platform/traefik/apply` | Render `01-stack-core.yml` for `base_domain` |

## REST APIs — dev stacks

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/dev-stacks` | List stacks + state |
| POST | `/api/dev-stacks` | Create (`id`, `preset` \| `template` \| `components[]`, optional `sample_data`) |
| POST | `/api/dev-stacks/<id>/action` | `start` \| `stop` \| `destroy` \| `repair` \| `reinstall` (`redeploy` alias) |
| POST | `/api/dev-stacks/<id>/action/stream` | Same actions; NDJSON `{type:log,text}` then `{type:done,result}` |
| GET | `/api/dev-stacks/<id>/snapshot` | Connection endpoints |
| GET | `/api/dev-stacks/<id>/access` | Networking, quick links, credentials, data stores (UI card) |
| GET | `/api/dev-stacks/<id>/config` | Paths + editable file list |
| GET/POST | `/api/dev-stacks/<id>/files` | Read/write stack files under `platform/dev-stacks/<id>/` |
| GET | `/api/dev-stacks/<id>/related-files/<file_id>` | Shared Traefik/platform files (read-only or edit) |
| POST | `/api/dev-stacks/<id>/reset-admin` | Reset WordPress/Magento admin (control token) |
| POST | `/api/hosted-apps/<slug>/platform-binding` | Set `platform.devStackId` on manifest |

Operator guide: [Platform tab & dev stacks](help:dash-platform).

## Dev stack generator

| Module | Role |
|--------|------|
| `dashboard/dev_stack_compose.py` | Catalog → `platform/dev-stacks/<id>/docker-compose.yml` |
| `dashboard/dev_stack_templates.py` | Ready stacks (WordPress, Magento, …) |
| `dashboard/dev_stack_frameworks.py` | Framework presets (Laravel, Django, …) |
| `dashboard/dev_stacks.py` | Lifecycle + snapshot |
| `dashboard/dev_stack_stream.py` | Streaming repair / reinstall / start |
| `dashboard/dev_stack_redeploy.py` | Regenerate files, config updates |
| `dashboard/dev_stack_access.py` | Card panels (networking, creds, links) |
| `dashboard/dev_stack_binding.py` | `docker-compose.leco-devstack.yml` overlay on register/deploy |

## Manifest schema

`tools/deploy-cli/leco_app/schema.py`:

```yaml
platform:
  devStackId: billing
  toolchain:
    node: "20"
```

## Traefik

- `scripts/render-platform-traefik.py` — rewrite `*.lh` hosts to `base_domain`
- `hosting/traefik/20-dev-stacks.yml` — per-stack routes (regenerated on destroy)
- `ecosystem-stack/services/traefik.sh` — selects static config by `tls.mode` (ACME uses `traefik-static-acme.yaml`)

## CLI (`leco-devops`)

From `tools/deploy-cli/` with `LECO_ECOSYSTEM_ROOT` set:

```bash
leco-devops platform show
leco-devops dev-stack create billing --preset level-1
leco-devops dev-stack start billing --stream
leco-devops dev-stack repair billing
leco-devops platform bind billing -f hosting/app-available/myapp/leco.app.yaml
```

Implementation: `tools/deploy-cli/leco_app/ecosystem_platform.py` imports dashboard modules after `bootstrap_dashboard()`.

Operator help: [Platform tab & dev stacks](help:dash-platform) · [Deploy CLI](../../DEPLOY_CLI.md).

## Tests

```bash
python3 -m pytest dashboard/tests/test_platform_config.py \
  dashboard/tests/test_dev_stack_compose.py \
  dashboard/tests/test_dev_stack_frameworks.py \
  dashboard/tests/test_dev_stack_all_presets.py -q
```

## Related docs

- [DEV_STACK_ISOLATION.md](../DEV_STACK_ISOLATION.md)
- [CLOUD_VM_DEPLOYMENT.md](../CLOUD_VM_DEPLOYMENT.md)
- [SRS_CLOUD_VM_PLATFORM.md](../SRS_CLOUD_VM_PLATFORM.md)
