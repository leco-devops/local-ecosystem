# Platform tab

The **Platform** tab manages **cloud/local deployment settings**, **ecosystem service bundles**, and **isolated dev stacks** — separate Docker Compose projects for databases, CMS demos, and framework sandboxes.

Open **`https://localhost.lh/?tab=platformTab`** (or click **Platform** in the top nav when visible).

> On a **cloud VM**, set `deployment_mode: cloud` and `base_domain` in `config/leco-platform.yaml`. See [Cloud VM deployment](help:cloud-vm-deployment).

## What you can do here

| Area | Purpose |
|------|---------|
| **Platform settings** | Edit `config/leco-platform.yaml` — domain, TLS mode, enabled services |
| **Ecosystem bundles** | Start/stop groups of stack services (Traefik, Postgres, AI, CF-local, …) |
| **Dev stack builder** | Create isolated stacks from presets or custom components |
| **Your dev stacks** | Operate each stack: networking diagram, credentials, lifecycle actions |

Destructive actions require the same **control token** as the **Control** tab when `DASHBOARD_CONTROL_TOKEN` is set.

---

## Dev stack builder

Expand **Dev stack builder** (cyan panel at the top).

1. **Stack ID** — lowercase slug (e.g. `billing`, `magento-full`). Becomes hostname `https://<id>.lh` locally.
2. **Quick preset** — pick a bundle:
   - **Infrastructure levels** — Postgres/MySQL/Redis/Node/Python combinations
   - **Common bundles** — LAMP, MEAN, data stores, Java services, …
   - **Ready application stacks** — WordPress, WooCommerce, Joomla, Drupal, Ghost, Magento (min/full), Elasticsearch
   - **Application frameworks** — Yii2, CakePHP, Symfony, Laravel, Django, Rails, NestJS, FastAPI, Flask, Express
3. **Sample data** — when the preset supports it (WordPress, Magento, …), auto-install a demo site on first **Start**.
4. **Custom components** — check components and versions from the catalog instead of a preset.
5. Click **Create stack**.

Files are written under **`platform/dev-stacks/<id>/`** (`docker-compose.yml`, `stack.yaml`). The stack is registered in **`config/leco-platform.yaml`**.

---

## Your dev stacks — stack card

Each stack is a full-width card with:

| Panel | Contents |
|-------|----------|
| **Networking** | Traefik → edge → app → data flow; public host `http://<stackId>.lh` |
| **Admin & credentials** | Default logins, **Open admin**, **Copy magic link**, **Reset credentials** (WordPress/Magento) |
| **Quick open** | Storefront, Adminer, Redis Commander, etc. |
| **Data stores** | DB name, user, password, internal Docker DNS endpoints |
| **Notes** | Template-specific hints (first boot, CLI commands) |

Below the panels:

- **Advanced — configuration & files** (violet accordion) — paths, edit stack `docker-compose.yml`, view shared Traefik/platform files.
- **Action buttons** — color-coded lifecycle controls (see below).
- **Operation log** — appears during **Start**, **Stop**, **Repair**, **Reinstall**, or **Destroy** with live compose output.

---

## Lifecycle actions

| Button | Color | When to use |
|--------|-------|-------------|
| **Start** | Green | Bring the stack up (`compose up -d`). Runs image preflight and URL repair for CMS stacks. |
| **Stop** | Amber / red when active | Stop containers; **keeps volumes and data**. |
| **Repair** | Blue | Fix config in place: image rewrites, Traefik routes, `lh-network`, `compose up`, public URL repair. **Keeps volumes** and **manual Advanced edits**. |
| **Reinstall** | Violet | Regenerate from template, `compose down -v`, fresh deploy. **Wipes DB/app data** — use after wrong DB version or broken install. |
| **Destroy** | Red | Remove stack completely: volumes, `platform/dev-stacks/<id>/`, registry entry, Traefik routes. |

**Choosing Repair vs Reinstall**

- **502 / routing / wrong image name** → **Repair** first.
- **Wrong MariaDB major, corrupted DB, need clean CMS install** → **Reinstall** (or **Destroy** then create again).
- **Remove stack from the machine** → **Destroy**.

The UI streams logs via **`POST /api/dev-stacks/<id>/action/stream`** (NDJSON). Buttons disable while an action runs on that row.

---

## Application frameworks

Framework presets run an **app container** plus the right database. First **Start** may take several minutes while dependencies install inside the container.

```bash
docker compose -p leco-devstack-<id> logs -f app
```

Public URL: **`http://<stackId>.lh`** (Traefik on `lh-network`).

---

## Bind a hosted app to a dev stack

In **`leco.yaml`** (or bridge manifest):

```yaml
platform:
  devStackId: billing
```

On register/deploy, LEco can inject stack connection env and attach the app compose project to the stack network. In the dashboard **Hosted apps** tab, use **Dev stack binding** when offered.

See [Dev stack isolation](../DEV_STACK_ISOLATION.md) and [Hosted apps](help:hosted-apps).

---

## Cloud VM notes

| Local (`*.lh`) | Cloud (`base_domain`) |
|----------------|------------------------|
| `http://wordpress.lh` | `https://wordpress.dev.example.com` |
| mkcert TLS | ACME / static / Cloudflare TLS modes |
| Platform tab still manages dev stacks the same way | DNS must point `*.<base_domain>` to the VM |

Install: [Cloud VM deployment](help:cloud-vm-deployment) · Operator doc: [CLOUD_VM_DEPLOYMENT.md](../CLOUD_VM_DEPLOYMENT.md).

---

## CLI equivalent

Set `LECO_ECOSYSTEM_ROOT` to your local-ecosystem checkout:

```bash
leco-devops platform presets
leco-devops dev-stack create wordpress --preset wordpress --sample-data
leco-devops dev-stack start wordpress --stream
leco-devops dev-stack repair magento-full
leco-devops dev-stack reinstall magento-full -y
```

See [LEco CLI](help:cli-basics) and [Deploy CLI](../../DEPLOY_CLI.md).

## Related

- [Dashboard tour](help:dash-overview)
- [Control tab](help:dash-control) — ecosystem services (not per dev stack)
- [Cloud VM deployment](help:cloud-vm-deployment)
- [502 / routing](help:ts-502) — when a stack URL fails
- Developer's guide → [Platform cloud APIs](help:dev-platform-cloud)
