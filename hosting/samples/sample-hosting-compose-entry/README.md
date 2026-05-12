# Sample: hosting-only compose entry (no upstream edits)

Use this when the **upstream repository** publishes host ports you cannot use on LEco — for example **`0.0.0.0:80`** (Traefik or another edge stack already uses **80/443**) or **`0.0.0.0:5432`** (another Postgres or compose stack already publishes **5432**). Keeps all LEco-specific compose patches under **`hosting/app-available/<slug>/`**.

## Mechanism

1. Bridge **`leco.app.yaml`** uses **`root: source`** where **`source`** is a symlink (or directory) to the upstream checkout inside the hosting folder.
2. Add **`docker-compose.leco-entry.yml`** next to **`leco.app.yaml`**. That file **`include`s** the real stack from **`source/docker-compose.yml`** or **`source/docker-compose.yaml`** (path is relative to this file’s directory — **must match the filename in the repo**).
3. Patch **each** upstream service that defines **`ports:`** on the host: use **`ports: !reset []`** so nothing is published on the host. Containers still talk to Postgres/Redis on the **internal** Docker network (**`postgresql:5432`**, etc.). For the app edge service, also join **`lh-network`** so Traefik can reach it. (Compose merges lists additively; **`!reset`** clears inherited **`ports`** — requires a recent Docker Compose / Compose spec implementation.)

4. In **`leco.yaml`**:

```yaml
infrastructure:
  dockerCompose:
    composeFileFromManifest: docker-compose.leco-entry.yml
```

When **`composeFileFromManifest`** is set, **`composeFile`** under the resolved app root is **not** passed as the first **`-f`** — only the hosting entry file and any **`additionalComposeFiles`** / **`additionalComposeFilesFromManifest`** entries are used.

## Files

| File | Purpose |
|------|---------|
| `docker-compose.leco-entry.example.yml` | Generic template; replace **`my-web`** / **`my-database`** and **`include.path`**. |
| `docker-compose.leco-entry.headwind.example.yml` | **Headwind** — **`hmdm`** + **`postgresql`**, **`include: source/docker-compose.yaml`**. Copy to **`docker-compose.leco-entry.yml`**. |

## Headwind / HMDM-style stacks

Use **`docker-compose.leco-entry.headwind.example.yml`** as a starting point. You need **both**:

- **`postgresql`** → **`ports: !reset []`** (avoids host **:5432** conflicts).
- **`hmdm`** → **`ports: !reset []`** **and** **`lh-network`** (avoids host **:80** vs Traefik; Traefik reaches the container on the overlay network).

If **`headwind-postgresql-1`** starts but **`headwind-hmdm-1`** fails with **`Bind for 0.0.0.0:80`**, the merged project still publishes **80** for **`hmdm`** — your entry file is missing the **`hmdm`** block, the service key does not match upstream (must be exactly **`hmdm`**), or **`!reset` is unsupported** (upgrade Docker Desktop / `docker compose` v2.24+).

### Verify before deploy

From **`hosting/app-available/<slug>/`** (where the entry file and **`source`** symlink live):

```bash
docker compose -f docker-compose.leco-entry.yml config
```

Inspect the **`hmdm`** service: there must be **no** host port (**`published: "80"`**). If **80** is still there, the **`hmdm`** patch is missing, the service name does not match upstream, or your **`docker compose`** build is too old for **`!reset`** (use Docker Compose **v2.24+**).

**`leco.yaml`** must set **`composeFileFromManifest: docker-compose.leco-entry.yml`** (and no conflicting extra **`-f`** that reintroduces upstream as the *first* file without your patches).

Compose variable warnings (**`SQL_USER`**, **`HMDM_URL`**, …) mean the upstream stack expects env vars. Add **`infrastructure.dockerCompose.envFile`** in **`leco.yaml`** pointing to e.g. **`.env.leco`** beside the manifest with the keys Headwind documents — keep that file gitignored.

## See also

- **`hosting/samples/sample-leco-hosting-overlay/`** — lighter pattern (**`composeFile`** + **`additionalComposeFilesFromManifest`**) when a simple overlay is enough and you do **not** need to remove upstream **`ports`**.
- **[docs/DEPLOY_CLI.md](../../docs/DEPLOY_CLI.md)** — **`composeFileFromManifest`** field reference.
- **[docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md](../../docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md)** — port **80** conflicts and Traefik.
