# Platform cloud APIs (developer)

Cloud VM platform features expose dashboard APIs and shell helpers.

## Config files

| File | Role |
|------|------|
| `config/leco-platform.yaml` | Deployment mode, domain, TLS, enabled services, dev stack registry |
| `ecosystem-stack/config/install-profiles.yaml` | Profile definitions |
| `ecosystem-stack/config/component-catalog.yaml` | Dev stack component versions |
| `ecosystem-stack/lib/platform_config.py` | Profile resolution, `enabled-services` CLI |

## REST APIs

| Method | Path | Purpose |
|--------|------|---------|
| GET/POST | `/api/platform/config` | Read/write platform YAML |
| GET | `/api/platform/catalog` | Component catalog |
| GET | `/api/platform/services` | Ecosystem service status |
| POST | `/api/platform/services/<id>/action` | `start` / `stop` |
| POST | `/api/platform/traefik/apply` | Render `01-stack-core.yml` for `base_domain` |
| GET/POST | `/api/dev-stacks` | List / create stacks |
| POST | `/api/dev-stacks/<id>/action` | `start` / `stop` / `destroy` |
| GET | `/api/dev-stacks/<id>/snapshot` | Stack-scoped connection endpoints |

## Dev stack generator

- `dashboard/dev_stack_compose.py` — catalog → `platform/dev-stacks/<id>/docker-compose.yml`
- `dashboard/dev_stacks.py` — lifecycle + snapshot
- `dashboard/dev_stack_binding.py` — `docker-compose.leco-devstack.yml` overlay on register/deploy

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
- `ecosystem-stack/services/traefik.sh` — selects static config by `tls.mode` (ACME uses `traefik-static-acme.yaml`)

## Tests

```bash
python3 -m pytest dashboard/tests/test_platform_config.py dashboard/tests/test_dev_stack_compose.py -q
```
