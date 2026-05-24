# Control tab

**Control** runs lifecycle actions against ecosystem services defined in `ecosystem-stack/services/*.sh`.

## Actions

| Action | Effect |
|--------|--------|
| **Start** | `docker run` / service `start()` |
| **Stop** | Stop container |
| **Restart** | Stop + start |
| **Pause / Unpause** | Docker pause |
| **Remove** | Remove container (volumes policy per service) |
| **Reset** | Remove container + delete data volumes (destructive) |
| **Deploy** | Service-specific deploy or restart |
| **Bulk** | All services except dashboard (+ optional platform skip) |

## Platform skip

Bulk stop/restart **skips** `traefik` and `postgres` by default so routing and n8n DB stay up. Override with env `ECOSYSTEM_BULK_PLATFORM_SKIP`.

## File transfer (FTP / SFTP)

Under **Infra add-ons & file transfer**:

| Target | Effect |
|--------|--------|
| **File transfer (FTP, SFTP)** | Whole `file-transfer/docker-compose.yml` |
| **SFTP** / **FTP** / **File transfer browser** | Individual compose services |

Credentials and SFTP public keys: **Service hubs → UI access** ([guide](help:file-transfer)).

## AirLLM / Ollama from Control

You can start/stop **`airllm`** and **`ollama`** here, but **model install/load** belongs in **Infrastructure → Model manager** or `leco-cli.sh ollama|airllm …`.

## CLI equivalent

```bash
./ecosystem-stack/ecosystem-stack.sh start airllm
./ecosystem-stack/ecosystem-stack.sh stop ollama
./leco-cli.sh stack status
```

Back: [Dashboard tour](help:dash-overview)
