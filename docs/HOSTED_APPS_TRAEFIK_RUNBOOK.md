# Hosted apps — Traefik & `*.lh` runbook

This document records **common failures** seen with **LEco DevOps Hosted apps** (compose stacks behind Traefik), **root causes**, and **fixes** implemented in this repository. Use it with **[DEPLOY_CLI.md](DEPLOY_CLI.md)**, **[LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md)**, and **[DEPLOYMENT.md](DEPLOYMENT.md)** §12.

---

## 1. Symptom → cause → fix (summary)

| Symptom | Typical cause | Fix in repo / operator action |
|--------|----------------|------------------------------|
| **502** from Traefik; compose `ps` healthy | App containers not on **`lh-network`** (Traefik is on that network only) | Add **`hosting/app-available/<slug>/docker-compose.leco-hosting.yml`** joining **`lh-network`**, list it under **`infrastructure.dockerCompose.additionalComposeFilesFromManifest`**, **redeploy** compose. **`ensure_lh_network_hosting_overlay()`** in `dashboard/leco_detect.py` (Register / Validate) can create the file and profile entry when routing + compose are present. |
| **502**; wrong upstream name | Traefik **`loadBalancer`** URL does not match **Docker DNS** for that service | Use **`container_name`** from the compose file if set; otherwise default **`{project}-{service}-1`** (Compose v2+). **`_compose_service_backend_host()`** in `dashboard/leco_detect.py` encodes this. **`normalize_profile_compose_backend_hosts()`** rewrites ambiguous hosts. |
| **502** after renaming registry id | **`routing.entries`** still use another app’s prefix (e.g. **`cv-frontend`** while **`projectName`** is **`cvision`**) | **`_remap_stale_compose_dns_host()`** / **`_compose_service_key_from_routing_host()`** remap wrong-prefix hosts to the canonical name for **`dockerCompose.projectName`**. Run **Validate** or **Register** to persist. |
| UI **OK** but dashboard shows **“HTTP 0”** or bogus API status for **`https://*.lh`** | URL probes used **`https://traefik/...`** internally; Traefik is reached on **HTTP :80** inside Docker | **`get_probe_target()`** in `dashboard/monitor.py` now uses **`http://traefik{path}`** with **`Host: <app>.lh`** for all `*.lh` URLs (http and https). **Restart LEco DevOps** after upgrading. |
| SPA calls **`http://localhost:8001/api`** while opened on **`https://<app>.lh`** | Upstream **`docker-compose.yml`** sets **`REACT_APP_BACKEND_URL=http://localhost:8001`** (wins over same-origin logic) | Hosting overlay sets **`REACT_APP_BACKEND_URL: ""`** and **`REACT_APP_SITE_URL: https://<app>.lh`** (see **`_lh_overlay_env_for_service()`** in `dashboard/leco_detect.py` and **`hosting/samples/sample-leco-hosting-overlay/`**). **CrawlerVision** also prefers **`window.location.origin`** on **`*.lh`** in **`frontend/src/config/api.js`** (upstream repo). |
| Dashboard detail panel shows **"main URL 502"** but per-URL probes return **200** | Detail view reads `main_url_probe` from the **cached sidebar list** (`/api/hosted-apps`), which can be stale (90 s client cache + localStorage). The fresh per-app snapshot (`url_probes`) is ignored. | `dashboard/static/dashboard.js` now prefers the fresh `url_probes[main_url]` from the snapshot over the cached `app.main_url_probe` when rendering the detail status dot. |
| Register wizard: changing **App id (slug)** does not update Public URL hostnames | URLs were populated by **Detect** (which derives the hostname from the folder name, e.g. `cloudflare.lh`), not from the slug. No input listener synced them. | `dashboard/static/dashboard.js` adds an `input` listener on the slug field that reads the *actual* hostname from the first URL row and rewrites all rows when the slug changes (preserving custom paths like `/api`, `/graphql`). |
| **`Bind for 0.0.0.0:80` / `:5432` / … **`port is already allocated`** on **`docker compose up`** | Upstream compose publishes **host ports** (web **80**, Postgres **5432**, etc.) that another process or stack already uses | Keep the upstream repo untouched: **`docker-compose.leco-entry.yml`** with **`composeFileFromManifest`**, **`include`** of **`source/docker-compose.yml`** (or **`.yaml`**), then **`ports: !reset []`** on **every** service that published ports (e.g. **`hmdm`** + **`postgresql`** for Headwind); attach **`lh-network`** only where Traefik must reach the service. See **`hosting/samples/sample-hosting-compose-entry/`**. Unset **`SQL_*` / `HMDM_*`** warnings → add **`envFile`** under hosting or a **`.env`** beside the entry compose. |
| **`/api/geo/ip-country`** **404** in Docker but works on Cloudflare | Route existed only on the **Worker** (`request.cf.country`), not in **FastAPI** | **CrawlerVision** **`backend/server.py`** adds **`GET /api/geo/ip-country`** for Docker (headers, **`GEO_IP_DEV_COUNTRY`**, optional **ip-api** lookup). |

---

## 2. Correct routing shape (split UI + API)

For **React + API** on one hostname:

- **`leco.yaml`** → **`infrastructure.routing.entries`**: **`hostname`**, **`apiPathPrefix`** (often **`/api`**), **`frontend`**, **`apiBackend`** (host + port).
- **`leco-app traefik-fragment`** / merge produces **higher-priority** routers for **`Host(...) && PathPrefix(/api)`** → API container, and **lower-priority** **`Host(...)`** → UI container (`tools/deploy-cli/leco_app/traefik_fragment.py`).
- Traefik forwards the **full path** (including **`/api`**) to the backend; the API must mount routes under **`/api`** (same as direct `localhost:8001` usage).

---

## 3. Operator checklist (new or broken app)

1. **`dockerCompose.projectName`** matches **`docker compose -p`** (stable DNS prefix when **`container_name`** is absent).
2. **`routing.entries`** upstream hosts match **`container_name`** or **`{project}-{service}-1`**.
3. **`additionalComposeFilesFromManifest`** includes **`docker-compose.leco-hosting.yml`** (or equivalent) so **frontend** and **api** services join **`lh-network`**.
4. **`docker compose … up -d`** uses **both** the upstream **`-f`** and the hosting overlay **`-f`** (LEco DevOps deploy does this when the profile is correct).
5. **`hosting/traefik/dynamic.yml`** merged (Register / **`leco-app ecosystem-register --merge-traefik`**).
6. For **same-origin API** on `*.lh`, overlay env clears hardcoded **`localhost`** API base vars (framework-specific).
7. After changing **dashboard** probe logic, **restart** the **`service-dashboard`** container.

---

## 4. Code map (automation & UI)

| Concern | Location |
|--------|----------|
| Compose DNS / normalization / wrong-prefix remap | `dashboard/leco_detect.py` (`_compose_service_backend_host`, `_normalize_compose_routing_backend_hosts`, `_remap_stale_compose_dns_host`, `ensure_lh_network_hosting_overlay`) |
| Register / Validate auto-heal | `dashboard/leco_registration.py`, `dashboard/leco_validate.py` |
| `*.lh` URL probes | `dashboard/hosted_apps.py` (`_probe_main_url`, `_probe_url_map`), `dashboard/monitor.py` (`get_probe_target`, `check_url`) |
| Fresh probe override in detail panel | `dashboard/static/dashboard.js` — snapshot `url_probes` → `app.main_url_probe` before `hostedMainUrlProbeSummary()` |
| Slug → URL auto-sync (register wizard) | `dashboard/static/dashboard.js` — `_extractHostFromUrlRows()`, `idIn` input listener |
| Compose `-f` chain | `tools/deploy-cli/leco_app/compose_runner.py` |
| Traefik fragment | `tools/deploy-cli/leco_app/traefik_fragment.py` |
| Example overlay | `hosting/samples/sample-leco-hosting-overlay/`, `hosting/app-available/cvision/docker-compose.leco-hosting.yml` |

---

## 5. Reference apps in this repo

- **`hosting/app-available/cvision/`** — bridge + profile + **`docker-compose.leco-hosting.yml`** for **CrawlerVision**-style **frontend** / **backend** + **`cv-frontend`** / **`cv-backend`** **`container_name`** in upstream compose (Traefik targets **`cv-frontend`**, **`cv-backend`**, not **`cvision-frontend-1`**, when those names are set).

---

## 6. Related documentation

- **[DEPLOY_CLI.md](DEPLOY_CLI.md)** — `additionalComposeFilesFromManifest`, split routes, same-origin **`/api`** note.
- **[LECO_USER_MANUAL.md](LECO_USER_MANUAL.md)** — Hosted apps tab, troubleshooting.
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — §12 Troubleshooting, **`repair-network`**.
