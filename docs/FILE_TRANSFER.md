# FTP and SFTP file transfer

Local FTP and SFTP services for dev workflows (shared upload volume, default credentials for localhost only).

## Start / stop

```bash
./ecosystem-stack/services/file-transfer.sh start
./ecosystem-stack/services/file-transfer.sh stop
./ecosystem-stack/services/file-transfer.sh status
```

Or via the main stack CLI (when `file-transfer` is in `config/leco-platform.yaml` `enabled_services`, or unrestricted):

```bash
./ecosystem-stack/ecosystem-stack.sh start file-transfer
```

Dashboard **Control** tab: **File transfer (FTP, SFTP)** or per-service **SFTP** / **FTP** targets.

## Install profile

Profile **`file-transfer-full`** enables only Traefik, dashboard, and the file-transfer compose stack. Profile **`full`** includes file-transfer with all other ecosystem services.

```bash
LECO_INSTALL_PROFILE=file-transfer-full ./ecosystem-stack/install-foundation.sh
```

## Connection defaults

| Protocol | Host | Port | User | Password |
|----------|------|------|------|----------|
| SFTP | `localhost` or `sftp.lh` | **2222** (avoids macOS SSH on :22) | `leco` | `leco` |
| FTP | `ftp.lh` or `localhost` | **21** | `leco` | `leco` |

FTP passive mode uses ports **21100–21110** (published on the host). Set `FTP_PUBLICHOST=ftp.lh` (default) so clients receive correct PASV addresses.

Both services share the Docker volume **`file_transfer_data`** mounted at `/home/leco`.

## Configuration

Copy [`file-transfer/.env.example`](../file-transfer/.env.example) to `file-transfer/.env` to override ports, users, or passwords:

```bash
cp file-transfer/.env.example file-transfer/.env
# edit SFTP_USERS, FTP_USERS, SFTP_PORT, FTP_PORT, FTP_PUBLICHOST, …
./ecosystem-stack/services/file-transfer.sh restart
```

`SFTP_USERS` follows [atmoz/sftp](https://github.com/atmoz/sftp) format: `user:pass:uid:gid`.

## Client examples

```bash
# SFTP
sftp -P 2222 leco@localhost

# FTP (curl)
curl -u leco:leco ftp://localhost:21/ --ftp-pasv
```

Service hubs (credentials + connection strings): **http://localhost.lh/hub/sftp** and **http://localhost.lh/hub/ftp**.

## Read-only file browser

Browse uploads in the browser (list + download only; no upload/delete/rename):

| URL | Purpose |
|-----|---------|
| **http://files.lh** | Canonical file browser |
| **http://ftp-files.lh** | Alias (same shared FTP/SFTP volume) |
| **http://sftp-files.lh** | Alias (same shared FTP/SFTP volume) |

The **`leco-file-browser`** container mounts the upload volume **read-only** and nginx rejects non-GET/HEAD requests.

## Security

Defaults are for **trusted local development only**. Change credentials before exposing ports beyond localhost. Do not enable on internet-facing hosts without TLS (FTPS) or VPN restrictions.

## Compose layout

- **SFTP:** `atmoz/sftp:alpine` → container `leco-sftp`
- **FTP:** `delfer/alpine-ftp-server` → container `leco-ftp`
- **Browser:** nginx autoindex → container `leco-file-browser` (read-only)
- Compose file: [`file-transfer/docker-compose.yml`](../file-transfer/docker-compose.yml)
- Service script: [`ecosystem-stack/services/file-transfer.sh`](../ecosystem-stack/services/file-transfer.sh)

See also [`DEPLOYMENT.md`](DEPLOYMENT.md) and [Ecosystem stack developer guide](help/dev-05-ecosystem-stack.md).
