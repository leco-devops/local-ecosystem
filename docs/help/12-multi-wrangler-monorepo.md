# Multi-Wrangler monorepos (Workers + Pages)

Some applications keep **several** Wrangler configs under `infra/` (or similar), not a single root `wrangler.toml`:

```
my-monorepo/
  infra/wrangler.api.toml
  infra/wrangler.onboarding.toml
  infra/wrangler.onboarding-email.toml
  infra/wrangler.onboarding-mobile.toml
  infra/wrangler.pages.toml
  apps/dashboard/dist          # Pages static output
  package.json
```

LEco treats this as a first-class onboarding path — not a one-off workaround.

## Detect and generate

**Detect** (`POST /api/leco/detect` / `leco-devops detect`) scans the resolved tree and returns:

- `wrangler_configs[]` — every Worker-oriented `wrangler.*.toml`
- `wrangler_pages_config` — first Pages config, if any
- suggested `infrastructure.runtimes[]` with incrementing ports (8787, 8788, …)

**Generate YAML** writes:

- one **`runtimes[]`** entry per Worker (`type: cloudflare-workers`, `config: infra/wrangler….toml`)
- optional **`cloudflare-pages`** runtime for `wrangler.pages.toml`
- **`configRefs`** keys such as `wranglerApiConfig`, `wranglerOnboardingConfig`, `wranglerPagesConfig` (see `dashboard/leco_wrangler_paths.py`)

Set **`infrastructure.cloudflare.wranglerConfig`** to the primary API Worker (usually `infra/wrangler.api.toml`) for **provision-local-cf**.

## Config symlinks under hosting

When the app is **materialized** under `hosting/app-available/<slug>/` (`wsp:…` or zip flow), LEco creates:

```
hosting/app-available/myapp/
  source -> /workspace-parent/MyMonorepo   # or host path on workstation
  infra/wrangler.api.toml -> …/infra/wrangler.api.toml
  infra/wrangler.onboarding.toml -> …
  …
```

Symlink sources (`dashboard/hosting_layout.py` → `collect_materialized_config_symlink_paths`):

| Source | Paths mirrored |
|--------|----------------|
| `configRefs` | All bridge entries |
| `infrastructure.runtimes[].config` | Every runtime Wrangler file |
| Wrangler scan | Any extra `wrangler.*.toml` under resolved root |

**Re-run Generate YAML or Save YAML** after adding a new `infra/wrangler.*.toml` in the upstream repo so `leco.yaml`, `configRefs`, and symlinks refresh. Deploy uses `runtimes[]` from the profile; missing symlinks alone do not block containers.

### Host vs dashboard container

| Environment | `source` / config symlink targets |
|-------------|-----------------------------------|
| Dashboard container | `/workspace-parent/…` (read-only sibling mount) |
| Host `leco-devops` | Remapped to `$LECO_WORKSPACE_PARENT_HOST` or ecosystem parent + relative path |

Stale `/workspace-parent/…` symlinks on the host are replaced on the next sync when the remapped tree exists.

## Routing pattern

Typical Traefik split for API Worker + Pages dashboard:

```yaml
infrastructure:
  routing:
    entries:
      - hostname: myapp.lh
        upstream:
          - prefix: /api
            target: runtime
            runtime: api
          - prefix: /
            target: runtime
            runtime: dashboard
```

Runtime **`id`** values come from the Wrangler filename (`wrangler.api.toml` → `api`).

## Compose

Monorepos without upstream `docker-compose.yml` use the generated **`docker-compose.leco-runtime.yml`** only:

```yaml
infrastructure:
  dockerCompose:
    composeFileFromManifest: docker-compose.leco-runtime.yml
```

Do **not** set `composeFile: docker-compose.yml` unless that file exists in the upstream tree.

## Sample and Raven

- Reference sample: **`hosting/samples/sample-cf-multi-wrangler-monorepo/`**
- Working materialized app: **`hosting/app-available/raven/`** (UtilityServer/raven)

## Related

- [wsp: paths & materialize](help:onboarding-materialize)
- [Hosting layout](help:hosting-layout)
- [LECO_APP_BLUEPRINT.md](../LECO_APP_BLUEPRINT.md) §4.1
- [HOSTED_APPS_TRAEFIK_RUNBOOK.md](../HOSTED_APPS_TRAEFIK_RUNBOOK.md) §7 (edge runtimes)
