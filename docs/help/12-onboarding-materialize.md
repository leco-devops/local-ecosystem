# wsp: paths & materializing code into hosting

When your application lives in a **sibling repository** (or any read-only mount), LEco **materializes** manifests under `hosting/app-available/<slug>/` and links to the real tree with a **`source`** symlink. You do not copy the whole upstream repo into hosting.

## Path prefixes

| Prefix / form | Meaning |
|---------------|---------|
| `/project/...` | Path inside the ecosystem repo (writable when under `hosting/`). |
| `wsp:MyRepo/apps/api` | **Workspace parent** sibling: `DASHBOARD_WORKSPACE_PARENT` / `LECO_WORKSPACE_PARENT_HOST` — typically read-only. |
| Absolute host path | Mapped via `DASHBOARD_*_HOST` env vars on Docker Desktop. |

Browse roots: `GET /api/leco/browse?root=project|wsp`.

## Materialize workflow (dashboard)

1. **Register application** → path `wsp:MyMonorepo/services/web`.
2. **Detect** — LEco scans the resolved tree (compose, wrangler, Dockerfile, ports).
3. **Generate YAML** (control token):
   - Writes `hosting/app-available/<slug>/leco.app.yaml` and `leco.yaml`.
   - Creates **`source`** → resolved app directory on the sibling mount.
   - Adds **config symlinks** for paths in `configRefs` / detected configs (wrangler, `.env`, compose) when targets exist.
4. Registry will point at **`hosting/app-available/<slug>/leco.app.yaml`** (not the read-only path).

### `source` symlink rules

- Bridge uses **`root: source`** — the symlink **filename** under `app-available/<slug>/`, not a folder named `source` inside the upstream repo.
- If you pick a path ending in `source/` but `wrangler.toml` or `docker-compose.yml` live at the **repo root**, LEco promotes the symlink target to the **parent** so paths stay `wrangler.toml` not `../wrangler.toml`.

## Pointing compose and wrangler at the app tree

Paths in **`leco.yaml`** are relative to **resolved root** = `(directory of leco.app.yaml / manifest.root).resolve()` following symlinks.

| Field | Resolved from |
|-------|----------------|
| `infrastructure.dockerCompose.composeFile` | Resolved root (upstream tree via `source`) |
| `infrastructure.dockerCompose.additionalComposeFiles` | Resolved root |
| `infrastructure.dockerCompose.composeFileFromManifest` | Directory of `leco.app.yaml` (hosting slot) |
| `infrastructure.dockerCompose.additionalComposeFilesFromManifest` | Directory of `leco.app.yaml` |
| `infrastructure.cloudflare.wranglerConfig` | Resolved root |

Example bridge + profile for materialized app:

```yaml
# hosting/app-available/myapp/leco.app.yaml
lecoAppVersion: "3"
name: myapp
root: source
localHostProfile: leco.yaml
```

```yaml
# hosting/app-available/myapp/leco.yaml
infrastructure:
  dockerCompose:
    composeFile: docker-compose.yml
    additionalComposeFilesFromManifest:
      - docker-compose.leco-hosting.yml
```

## Snapshots are not live sync

Manifests under `hosting/app-available/` are **snapshots**. Changes in the sibling repo are **not** auto-synced into YAML or registry.

To refresh after upstream changes:

1. **Detect** again and **Save YAML**, or
2. Re-run **Register** if infrastructure blocks changed, or
3. Edit `leco.yaml` manually and **Save YAML**.

Re-deploy when compose or runtime config changed: [Deploy & rebuild](help:deploy-rebuild).

## CLI materialize

There is no separate `leco-devops materialize` command. Materialization happens via dashboard **Generate YAML** / **Save YAML**, or **`leco-devops scaffold`** which copies a sample tree into `app-available/<slug>/`.

Register always reads manifests from disk:

```bash
leco-devops ecosystem-register -E "$LECO_ECOSYSTEM_ROOT" \
  --registry-manifest-relpath hosting/app-available/myapp/leco.app.yaml \
  --merge-traefik
```

## Zip instead of wsp:

Upload a zip → `hosting/app-available/<slug>/` contains the full tree (no `source` symlink required). Use **Detect** then **Register**.

## Related

- [Hosting layout](help:hosting-layout)
- [Overriding upstream apps](help:hosting-overrides)
