# FTP & SFTP file transfer

Local **FTP**, **SFTP**, and a **read-only web file browser** for dev workflows. All three share one Docker volume (`file_transfer_data` at `/home/leco`).

## Where to manage it

| Surface | What you do |
|---------|-------------|
| **Control** tab | Start/stop/restart **File transfer (FTP, SFTP)** or individual **SFTP** / **FTP** / browser targets |
| **Infrastructure** tab | Section **2b · File transfer** — live status, connect strings, links to service hubs |
| **Service hubs → UI access** | Copy-paste credentials, **Edit** passwords or SFTP public keys, **Reset & apply** |
| **Service hubs** `/hub/sftp`, `/hub/ftp`, `/hub/files` | Per-service notes, connection strings, container metrics |

Help link: [Control tab](help:dash-control) · [Infrastructure tab](help:dash-infra)

## Default connection (local dev)

| Protocol | Host | Port | User | Password |
|----------|------|------|------|----------|
| **SFTP** | `localhost` or `sftp.lh` | **2222** | `leco` | `leco#localhost-192` |
| **FTP** | `localhost` or `ftp.lh` | **21** | `leco` | `leco#localhost-192` |

FTP passive mode uses host ports **21100–21110**. Defaults are for **trusted localhost only** — change before exposing beyond your machine.

## Start the stack

```bash
./ecosystem-stack/services/file-transfer.sh start
# or
./ecosystem-stack/ecosystem-stack.sh start file-transfer
```

Dashboard **Control** → **Infra add-ons & file transfer** → **Start FTP / SFTP stack**.

Install profile **`file-transfer-full`** (Traefik + dashboard + file transfer only):

```bash
LECO_INSTALL_PROFILE=file-transfer-full ./ecosystem-stack/install-foundation.sh
```

## Connect from your machine

```bash
# SFTP (password — you will be prompted)
sftp -P 2222 leco@localhost

# SFTP (public key)
sftp -P 2222 -i ~/.ssh/id_ed25519 leco@localhost

# FTP (curl; quote password because of #)
curl -u 'leco:leco#localhost-192' ftp://localhost:21/ --ftp-pasv
```

## Browse uploads in the browser

Read-only directory listing (GET/HEAD only):

| URL | Notes |
|-----|--------|
| **http://files.lh** | Canonical browser |
| **http://ftp-files.lh** | Alias (same volume) |
| **http://sftp-files.lh** | Alias (same volume) |

Uploads via FTP or SFTP appear here automatically.

## Credentials from the dashboard

Open **Service hubs → UI access** (`/hub#hub-ui-access`):

- **SFTP / FTP** — full **User**, **Password**, **Host**, **Port**, and connection strings with **Copy** buttons.
- **Edit** — change username/password/**port**; for SFTP choose **password**, **public key only**, or **both**.
- **Reset & apply** — restore compose defaults and recreate containers (control token when set).

See also [UI credential vault](../../UI_CREDENTIAL_VAULT.md) in the Docs tab.

## SFTP public-key auth

1. **Service hubs → UI access → SFTP → Edit**
2. Set **Authentication** to **Public key only** or **Password + public key**
3. Paste an OpenSSH public key (`ssh-ed25519 AAAA…`)
4. Save — dashboard writes `file-transfer/keys/sftp/leco.pub` and recreates `leco-sftp`

## Typical dev workflow

1. Start file-transfer stack from **Control** or CLI.
2. Upload fixtures with SFTP/FTP to `/home/leco` (or a subfolder).
3. Open **files.lh** to confirm uploads.
4. Point a hosted app or script at `ftp://…` or mount the shared volume if co-located on `lh-network`.

## Troubleshooting

| Symptom | Check |
|---------|--------|
| Connection refused on :2222 / :21 | **Control** → start SFTP/FTP; `docker ps \| grep leco-` |
| Auth failed after password change | **UI access → Reset & apply** on SFTP/FTP, or edit `file-transfer/.env` and restart |
| FTP PASV hangs | `FTP_PUBLICHOST=ftp.lh` in `file-transfer/.env`; passive ports 21100–21110 open on host |
| Browser 404 | Start **file-browser** service; Traefik routes `files.lh` → `leco-file-browser:8080` |
| Key auth fails | `.pub` file under `file-transfer/keys/sftp/`; mode **Public key only** uses empty password in `SFTP_USERS` |

Deep reference: [FILE_TRANSFER.md](../../FILE_TRANSFER.md) (Docs tab) · Developer guide: [File transfer (developer)](help:dev-file-transfer)
