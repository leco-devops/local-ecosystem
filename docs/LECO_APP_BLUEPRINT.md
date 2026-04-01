# LEco application blueprint

**Audience:** operators, dashboard users, and contributors extending hosting, registration, or `leco-app`.

This document is the **canonical map** of how a third-party app is represented in local-ecosystem: files on disk, merge rules, hosting materialization, Docker Compose, Cloudflare-local, Traefik, and teardown. For day-to-day commands see **[LECO_USER_MANUAL.md](LECO_USER_MANUAL.md)** and **[DEPLOY_CLI.md](DEPLOY_CLI.md)**.

---

## 1. Terminology

| Term | Meaning |
|------|---------|
| **Bridge** | `leco.app.yaml` — ties LEco to an app: `name`, `root`, `localHostProfile`, optional `applicationVersion`, optional `configRefs`, optional `localhost.notes`. |
| **Profile** | `leco.yaml` (or path in `localHostProfile`) — `infrastructure`, `urls`, `lifecycle`, `archetype`, `notes`. |
| **Effective manifest** | Bridge + profile `infrastructure` merged (same as `leco-app` / `load_effective_manifest` in `tools/deploy-cli/leco_app/schema.py`). |
| **Resolved root** | Directory where compose / wrangler paths are relative to: `(manifest_path.parent / manifest.root).resolve()` after following symlinks. |
| **Materialized app** | Read-only app path (`wsp:…`): YAML lives under `hosting/app-available/<slug>/` with a `source` symlink to the real tree. |

---

## 2. Recommended layout (v3)

```
<app-or-hosting-slot>/
  leco.app.yaml          # bridge (lecoAppVersion: "3")
  leco.yaml              # profile: infrastructure.*, urls, lifecycle, …
  leco.local-cf.yaml     # optional; written by provision-local-cf / deploy (not hand-authored)
```

- Put **`infrastructure.dockerCompose`**, **`infrastructure.cloudflare`**, **`infrastructure.routing`** in **`leco.yaml`**, not on the bridge (legacy v2 allowed these on `leco.app.yaml`; v3 keeps the bridge thin).
- **`configRefs`** on the bridge (optional) lists human/tooling paths (wrangler, compose, `.env`, …) relative to resolved root; dashboard **Generate/Save YAML** can refresh **config symlinks** under materialized `app-available/<slug>/`.

---

## 3. Registry and hosting indirection

- **`config/leco-registry.yaml`** stores `id`, `label`, and **`manifest`** path relative to the ecosystem repo root.
- Materialized apps use paths like **`hosting/app-enabled/<slug>/leco.app.yaml`** (symlink → `../app-available/<slug>`).
- **`ecosystem-unregister`** removes the registry row and, when the manifest path is under **`hosting/`**, deletes **`hosting/app-enabled/<slug>`** and **`hosting/app-available/<slug>`** (`tools/deploy-cli/leco_app/ecosystem_registry.py`).

---

## 4. `source` symlink (read-only roots)

- Under **`hosting/app-available/<slug>/`**, **`source`** points at the **real app tree** (sibling repo path) so `root: source` on the bridge resolves correctly inside the ecosystem repo.
- If the wizard path ends in a directory named **`source/`** but **`wrangler.toml`** or **`docker-compose.yml`** live in the **parent** repo root, the dashboard promotes the symlink target to that **parent** (`compute_hosting_source_symlink_target` in `dashboard/leco_detect.py`).
- **`root: source`** on the bridge names the **symlink file** under materialization; it is **not** joined as `orig_root/source` on the read-only tree when computing paths for save/register.

See **`hosting/README.md`**.

---

## 5. Docker Compose

- **`infrastructure.dockerCompose.composeFile`** — primary file (relative to resolved root unless absolute).
- **`infrastructure.dockerCompose.additionalComposeFiles`** — optional list; `leco-app` / `docker compose` is invoked as **`-f` primary `-f` extra …** (later files merge/overrides per Compose rules).
- Extra compose definitions (e.g. **`leco.df.yaml`**) must exist **next to the primary file in the app repo** (resolved root). Files that exist only under **`hosting/app-available/...`** are **not** visible to Compose unless copied or symlinked into the app tree.
- If the primary compose file is **missing**, **`leco-app down`** exits **0** with a warning (stack treated as already removed); the dashboard **Remove** still runs **full offboard** afterward.

---

## 6. Cloudflare / Wrangler vs Docker

| Artifact | Role |
|----------|------|
| **`infrastructure.cloudflare.wranglerConfig`** | Path to `wrangler.toml`; **provision** and **deploy** hooks read this file. |
| **`infrastructure.wranglerBindingPreview`** | Informational mirror (KV/R2/D1 rows) for UI; **does not** create Docker services. |
| **Local CF provision** | Creates namespaces/buckets/databases on **kv.lh / r2.lh / d1.lh** adapters; writes **`leco.local-cf.yaml`**. |

---

## 7. Traefik

- **`ecosystem-register --merge-traefik`** merges **routing-derived** keys from the **effective** manifest into **`traefik/dynamic.yml`**.
- In v3, **`infrastructure.routing`** normally lives in **`leco.yaml`**. If neither **`routing.entries`** nor **`cloudflare.localCfPublicPrefix`** is set, register logs that Traefik merge was skipped.
- The **Hosted apps** UI reads routing from the bridge file **and** from **`infrastructure.routing`** in the profile file (`dashboard/hosted_apps.py`).

---

## 8. Dashboard flows (registration)

| Step | API / behavior |
|------|----------------|
| Detect | `POST /api/leco/detect` — scan path for compose, wrangler, archetype. |
| Generate YAML | `POST /api/leco/generate-yaml` — write bridge + profile; materialized roots refresh **`source`** + **config symlinks**. |
| Save YAML | `POST /api/leco/save-yaml` — validate Pydantic schemas, same symlink rules; **Save** runs path normalization against resolved tree **after** refreshing **`source`**. |
| Register | `POST /api/leco/register` — requires YAML on disk; **`leco-app ecosystem-register`**; optional **`deploy`**. |

**Hosted apps list:** entries are loaded from the registry; **metadata** requires resolving **effective** manifest compose (bridge-only compose is insufficient for v3). Implementation: `dashboard/leco_control.py` (`parse_leco_effective_manifest_for_compose`, worker-only fallback). **Remove / Reset** on `leco-stack-<id>`: call **`leco-app ecosystem-unregister`**, which runs **`docker compose down`** (with **`-v`** on **Reset**) before Traefik / local CF / registry (`dashboard/control.py`, `dashboard/hosted_offboard.py`). **Remove from ecosystem** uses the same command, so containers are torn down with the registry.

---

## 9. Code map (maintainers)

| Concern | Location |
|---------|----------|
| Bridge/profile schema, merge | `tools/deploy-cli/leco_app/schema.py` |
| Compose CLI args | `tools/deploy-cli/leco_app/compose_runner.py` |
| Register/unregister, hosting dir removal | `tools/deploy-cli/leco_app/ecosystem_registry.py` |
| Dashboard scan, defaults, YAML materialize | `dashboard/leco_detect.py`, `dashboard/leco_materialize.py` |
| Register/deploy API | `dashboard/leco_registration.py`, `dashboard/app.py` |
| Hosting symlink helpers | `dashboard/hosting_layout.py` |
| Control remove/reset + offboard | `dashboard/control.py` |
| Offboard wrapper | `dashboard/hosted_offboard.py` |
| Hosted apps API | `dashboard/hosted_apps.py`, `dashboard/leco_control.py` |

---

## 10. Future work / extension ideas

- **Port detection** for `deploy` could scan **additional** compose files (today `detect_compose` focuses on the app root walk).
- **`ecosystem-unregister`** currently may abort registry removal if **local CF teardown** fails; operators can use **`--no-clean-local-cf`**; a **`--force`** that still unregisters is a possible enhancement.
- **Traefik** keys from profile-only manifests could be unified further in `traefik_manifest_keys` if new shapes appear.
- **Zip-uploaded** apps without a `source` symlink use the extracted tree as root; keep samples aligned in **`hosting/app-available/`**.

---

## See also

- **[hosting/README.md](../hosting/README.md)** — directory layout and zip upload.
- **[hosting/app-available/README.md](../hosting/app-available/README.md)** — bridge vs profile samples.
- **[tools/deploy-cli/README.md](../tools/deploy-cli/README.md)** — package overview and local CF policy.
