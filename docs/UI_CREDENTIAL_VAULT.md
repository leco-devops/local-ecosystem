# UI credential vault (local dev)

Store and apply login credentials for stack web UIs with real control panels (MinIO console, Adminer, n8n, Open WebUI) from **LEco DevOps**. **Local development only** — not for production.

## Setup

1. Copy the example vault:

   ```bash
   cp config/ui-credentials.example.yaml config/ui-credentials.yaml
   chmod 600 config/ui-credentials.yaml
   ```

2. Optional: set `DASHBOARD_CONTROL_TOKEN` and save the same value on the dashboard **Control** tab (required for save, reset, and auto-login).

3. Restart the dashboard after first deploy so new API routes load.

## Registry

Committed catalog: `ecosystem-stack/config/ui-login-registry.json`

Only services with a **real login control panel** or **protocol credentials managed by the stack** are listed (not adapter status pages, mail catchers, or APIs without a sign-in UI).

| Slug | UI | Auto-login | Reset apply |
|------|-----|------------|-------------|
| `n8n` | n8n | Server login + cookie on `n8n.lh` | Recreate `n8n` Postgres DB + wipe `n8n_data` + provision owner |
| `webui` | Open WebUI (`ai.lh`) | Server login + token in `localStorage` | Wipe `open-webui` volume + restart |
| `minio` | MinIO console | Server login + cookie on `minio-console.lh` | `mc` admin user + restart |
| `mysql` | Adminer → MySQL | Form POST on `adminer.lh` | `ALTER USER` + restart |
| `postgres` | Adminer → PostgreSQL | Form POST on `adminer.lh` | `ALTER USER` + restart |
| `sftp` | SFTP (`localhost:2222`) | — | Write `SFTP_USERS` in `file-transfer/.env` + recreate `leco-sftp` |
| `ftp` | FTP (`localhost:21`) | — | Write `FTP_USERS` in `file-transfer/.env` + recreate `leco-ftp` |
| `files` | Read-only browser (`files.lh`) | — | No credentials (browse-only) |

## Dashboard UI

- **Service hubs → UI access** (`/hub#hub-ui-access`) — full table with **Auto-login**, **Copy magic link**, **Open manual**, **Edit**, **Reset & apply**.
- **Per-service hub** (`/hub/<slug>`) — same actions for that service (SFTP/FTP show **Edit** / **Reset & apply** only; file browser is read-only).

**Auto-login** opens `/assist/login/<slug>?token=…` on the **service hostname** (e.g. `http://n8n.lh/assist/...`) so cookies and storage apply to the correct origin. Traefik routes `/assist` on `n8n.lh`, `ai.lh`, `minio-console.lh`, and `adminer.lh` to the dashboard.

**SFTP / FTP** have no web sign-in. The UI access table shows connection strings (host port, username) and links to the read-only file browser mirrors (`sftp-files.lh`, `ftp-files.lh`, `files.lh`). **Edit** saves to the vault and immediately writes `file-transfer/.env` and recreates the protocol container. **Reset & apply** restores compose defaults (`leco` / `leco`) the same way.

## Reset behavior

**Reset & apply**:

1. Writes compose-aligned defaults to `config/ui-credentials.yaml`
2. Applies password on the running container (`docker exec` / MinIO `mc`) where supported
3. Restarts the service container when configured, or wipes app volumes for n8n / Open WebUI

## APIs

| Method | Path | Auth |
|--------|------|------|
| GET | `/api/ui-credentials/catalog` | — |
| GET | `/api/ui-credentials/<slug>` | — |
| PUT | `/api/ui-credentials/<slug>` | Control token |
| POST | `/api/ui-credentials/<slug>/reset` | Control token |
| POST | `/api/ui-credentials/<slug>/launch-token` | Control token |
| GET | `/assist/login/<slug>?token=` | Token in query |

## Limitations

- **n8n** and **Open WebUI** use app-owned accounts. Dev defaults: `admin@local.lh` / `Localdev1` (n8n requires uppercase + number). **Reset & apply** wipes the app volume, recreates the container, and provisions the owner/admin via API when the service is ready.
- **MinIO** console login needs `MINIO_SERVER_URL=http://127.0.0.1:9000` inside the container (`cloudflare-local/docker-compose.yml`). **Reset & apply** recreates `minio` after updating credentials.
- Auto-login requires Traefik `/assist` routes (see `traefik/dynamic.yml`) and correct vault credentials.
