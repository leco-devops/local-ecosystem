# Multi-Wrangler monorepo (Workers + Pages)

Reference layout for a **pnpm/Node monorepo** with several Cloudflare Workers under `infra/wrangler.*.toml` and a Pages project at `infra/wrangler.pages.toml` — the same shape as production apps that keep Wrangler configs beside shared packages, not only a root `wrangler.toml`.

## What LEco does automatically

On **Generate YAML**, **Save YAML**, or **Register** (materialized `wsp:` apps):

1. **Detect** finds every `wrangler.*.toml` under the resolved app root (skips `node_modules`, `.wrangler`, etc.).
2. **Generate** writes one `infrastructure.runtimes[]` row per Worker config and optional `cloudflare-pages` runtime for Pages configs.
3. **`configRefs`** on the bridge lists each Wrangler file (`wranglerApiConfig`, `wranglerOnboardingConfig`, …) for humans and tooling.
4. **`sync_hosting_config_ref_symlinks`** mirrors those paths under `hosting/app-available/<slug>/` (e.g. `infra/wrangler.api.toml` → real file via `source`), including:
   - every `configRefs` entry;
   - every `infrastructure.runtimes[].config`;
   - any additional Wrangler files found by scan.

On the **host workstation**, symlinks that still point at `/workspace-parent/...` are refreshed to the sibling checkout (`UtilityServer/...`) when you re-run materialize; inside the dashboard container, `/workspace-parent/...` targets remain correct.

## Using this sample

**Self-contained (docs only):** files live in this folder; `root: .` resolves `infra/*` here.

**With a real sibling repo:**

```bash
leco-devops scaffold myapp -E "$LECO_ECOSYSTEM_ROOT" \
  --template sample-cf-multi-wrangler-monorepo \
  --source-path /absolute/path/to/your-monorepo
```

Then **Detect → Generate YAML → Register** with path `wsp:YourRepo` or `hosting/app-available/myapp`. Adjust `infrastructure.routing` so `/api` → `api` runtime and `/` → `dashboard` (Pages) runtime.

Workers-only stacks do not need upstream `docker-compose.yml`; set `infrastructure.dockerCompose.composeFileFromManifest: docker-compose.leco-runtime.yml` (auto-generated on register when `runtimes[]` is set).

See also:

- `hosting/samples/sample-cf-worker-runtime/` — single Worker + compose frontend
- `docs/LECO_APP_BLUEPRINT.md` §4.1 (config symlinks)
- `docs/help/12-multi-wrangler-monorepo.md`
