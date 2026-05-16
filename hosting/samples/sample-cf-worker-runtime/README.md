# Sample: local edge runtime (Cloudflare Workers)

When a production app's `/api/*` is implemented as a **Cloudflare Worker** (or any
edge runtime), routing `<slug>.lh/api` straight at a classic compose backend
will 404 the Worker-only routes. LEco DevOps fixes that **without changing the
upstream app**: declare an `infrastructure.runtimes[]` entry and a matching
`routing.entries[].upstream[]` rule, and LEco materializes a generic Wrangler
container next to your compose stack.

## What's in this folder

| File / dir | Purpose |
|------|---------|
| **`leco.app.yaml`** | Bridge manifest; uses the standard `root: source` symlink pattern. |
| **`leco.yaml`** | Localhost profile with `infrastructure.runtimes[]` + upstream-driven `routing.entries[]`. Documents `devVarsFile`, `stripBindings`, `productionOnlyBindings`, and the bootstrap directory layout. |
| **(generated)** `docker-compose.leco-runtime.yml` | Written by LEco beside the manifest on register / deploy. Gitignored. |
| **(generated)** `.dev.vars.example` | Auto-written by LEco on overlay materialization. Lists every UPPER_SNAKE `env.<NAME>` referenced in the Worker source that is NOT declared as a wrangler.toml `[vars]` key or binding. Grouped by vendor. **Existing files are never overwritten.** Gitignored. |
| **(operator-owned)** `.dev.vars` | Where you fill in actual secret values. LEco auto-bind-mounts it at `/app/.dev.vars` inside the runtime container whenever the file exists — `devVarsFile:` in `leco.yaml` is optional. Gitignored. |
| **(generated)** `.leco-runtime/<runtime_id>/wrangler.toml` | Sanitized in-container view of upstream wrangler.toml (bindings Wrangler local can't simulate removed). Gitignored. |
| **(operator-owned)** `.leco-runtime/<runtime_id>/d1-bootstrap-<BINDING>.sql` | Optional first-boot D1 schema. Useful when the upstream app keeps its base schema outside `migrations/` (e.g. in a TS constant `exec()`d from an admin endpoint). Gitignored. |

## How it works (automated onboarding pipeline)

1. Operator drops this dir into `hosting/app-available/<slug>/`, points the
   `source` symlink at the upstream repo, and runs the **registration wizard**
   (`leco-devops ecosystem-register` or the dashboard Register flow).
2. LEco DevOps reads `infrastructure.runtimes[]`, looks up the
   **`cloudflare-workers`** adapter under `dashboard/leco_runtimes/`, and
   writes `docker-compose.leco-runtime.yml` containing one service per runtime.
3. The adapter:
   - bind-mounts the upstream worker source at `/app` (read-only in spirit;
     write paths masked by LEco-owned named volumes for `node_modules` and
     `.wrangler`)
   - **sanitizes** `wrangler.toml` by stripping bindings Wrangler local cannot
     simulate (`[browser]` by default — see `stripBindings` in `leco.yaml`)
     and overlays the sanitized copy on top of `/app/wrangler.toml` *inside
     the container only*. Upstream `wrangler.toml` is never edited on disk.
   - **applies D1 bootstrap** SQL on first boot from
     `hosting/app-available/<slug>/.leco-runtime/<runtime_id>/d1-bootstrap-<BINDING>.sql`
     (or fallback `d1-bootstrap.sql`), tracked by a sentinel under
     `.wrangler/state/` so subsequent boots are no-ops.
   - runs **`wrangler d1 migrations apply`** in a resilient loop: when a
     single migration fails (e.g. an ALTER on a column the bootstrap already
     created), that file is marked as applied in `d1_migrations` and the
     batch retries — later migrations the operator actually cares about no
     longer get stranded.
   - finally execs `wrangler dev --local --persist-to .wrangler/state` so KV /
     R2 / D1 / queues are file-backed under the LEco volume.
4. Traefik routers (generated from `routing.entries[].upstream[]`) forward
   `https://<slug>.lh/api/*` to `leco-rt-<slug>-worker:8787`. The catch-all
   `/` rule keeps serving the SPA from its compose service. Router priority
   is derived from prefix length — longer prefixes (e.g. `/health/json`) win
   automatically without any manual priority math.

The **registration wizard** also surfaces a *detection hint*: it scans the
Worker entrypoint (`src/index.ts` / `worker.ts` / etc.) for `pathname === '…'`,
`pathname.startsWith('…')`, and router-call patterns (`app.get('…')`,
`router.post('…')`, …) and prints a copy-pasteable `routing.upstream` YAML
block. Re-run it on schema changes with `leco-devops runtimes -f leco.app.yaml`.

The same scan also enumerates **expected `.dev.vars` secrets** (every
UPPER_SNAKE `env.<NAME>` referenced in the Worker source that is not already
wired via `[vars]` or a binding) and reports `wired: N/M (missing: …)` in
both the wizard log and the CLI, so an operator can see at a glance which
keys still need values:

```text
expected .dev.vars secrets: 27  (wired: 0, missing: 27)
  .dev.vars not present yet — skeleton: hosting/app-available/<slug>/.dev.vars.example
  missing: ANTHROPIC_API_KEY, BREVO_SMTP_KEY, CF_API_TOKEN, …
```

Copy the auto-generated `.dev.vars.example` to `.dev.vars`, fill in real
values, re-deploy: every downstream feature that depends on those keys
flips from `down` to `healthy` in the Worker's `/health/json` board on the
next probe cycle, without a single change to the upstream Worker repo.

No changes to the upstream Worker repo, no host port collisions, no
"production has it / local doesn't" 404 drift. When the Worker 404s, the
local stack 404s — production fidelity by design.

## Other runtime types

The schema's `type:` field selects which adapter the registry uses. V1 ships
one fully-implemented adapter; the others are known types with stub adapters
so the schema shape stays stable:

| Type | Status | Image |
|------|--------|-------|
| `cloudflare-workers` | **Ready** | `leco/runtime-cloudflare-workers:latest` |
| `cloudflare-pages` | Roadmap | `leco/runtime-cloudflare-pages` |
| `vercel` | Roadmap | `leco/runtime-vercel` |
| `aws-lambda` | Roadmap | `leco/runtime-aws-lambda` |
| `deno-deploy` | Roadmap | `leco/runtime-deno-deploy` |

Run `leco-devops runtimes -f leco.app.yaml` to see the registry and what your
manifest declares.

## See also

- **`infra/runtimes/cloudflare-workers/`** — the Docker image powering this adapter.
- **`docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md`** — symptoms & fixes including the
  "Worker route 404s on `<slug>.lh`" pattern this sample resolves.
- **`docs/LECO_APP_BLUEPRINT.md`** — full schema reference including
  `runtimes[]` and `routing.entries[].upstream[]`.
