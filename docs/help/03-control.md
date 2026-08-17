# Control tab

**Operate → Control** runs lifecycle actions against ecosystem services defined in `ecosystem-stack/services/*.sh`, plus the compose stacks of registered hosted apps.

## How the page is laid out

Cards are grouped:

| Group | Contains |
|-------|----------|
| **Bulk & orchestration** | *All ecosystem stack services* — **Start all**, **Pause all**, **Unpause all**, **Stop all**, **Restart all**, **Redeploy all** |
| **Ecosystem stack & Traefik** | One card per stack service (Traefik, Postgres, Ollama, AirLLM, Open WebUI, n8n, Paperclip, MCP server, …) |
| **Infra add-ons & file transfer** | `infra/docker-compose.yml` (MySQL, Redis, Mailpit, Adminer, Redis Commander, Telegram gateway, cache lab) and `file-transfer/docker-compose.yml` (SFTP, FTP, read-only browser) |
| **Cloudflare local** | KV / R2 / D1 adapters, Workers runtime, browser rendering, autoscaler |

Registered compose applications are **not** here — they are on **[Hosted apps](help:hosted-apps)** (each also appears as a `leco-stack-<id>` control target).

## Actions

| Action | Effect |
|--------|--------|
| **Start** | `docker run` / service `start()` |
| **Stop** | Stop container |
| **Restart** | Stop + start |
| **Pause / Unpause** | Docker pause |
| **Remove** | Remove container (volume policy per service) |
| **Reset** | Remove container + delete data volumes (**destructive**) |
| **Deploy / Redeploy** | Service-specific deploy or recreate |

Destructive operations report what they did in the JSON response — read it rather than assuming.

## Default policies (per service)

Each card carries a segmented control setting that service's **default policy**:

| Policy | Meaning |
|--------|---------|
| **start** | Included in bulk operations |
| **stop** | Skipped by a bulk **start** |
| **offloaded** | Excluded from **all** bulk automation |

Policies affect **Start all / Stop all / Restart all** and `ecosystem-stack.sh start`.

## Platform skip

Bulk `stop`, `restart`, `redeploy`, `pause`, `remove`, `reset`, and `recreate` skip **LEco DevOps itself** and, by default, **Traefik** and **Postgres**, so routing and the shared database stay up while you cycle everything else. Override with the env var `ECOSYSTEM_BULK_PLATFORM_SKIP` (space-separated names).

To stop or redeploy Traefik, Postgres, or the dashboard itself, use the shell — see `docs/DEPLOYMENT.md` § *Core infra — shell*.

## UI credentials and auto-login

The **UI credentials (local dev)** row at the top of Control links to **Service hubs → UI access**, which is where passwords for MinIO, Adminer, n8n, Open WebUI, and SFTP/FTP live, along with short-lived **Auto-login** magic links. See **[FTP & SFTP file transfer](help:file-transfer)**.

## AirLLM / Ollama from Control

You can start/stop **`airllm`** and **`ollama`** here, but **model install / load** belongs in **Infrastructure → Model manager** or `leco-cli.sh ollama|airllm …`.

## Control token

When `DASHBOARD_CONTROL_TOKEN` is set in `ecosystem-stack/services/dashboard.sh`, every action on this page needs it — enter it once here and it is reused by **Hosted apps**, **CI/CD**, **Routes**, **Platform**, and the **AI providers** panel on Service hubs.

When the variable is **not** set, the control API is **unauthenticated**. Before this machine is reachable by anyone else, read **[PRODUCTION_HARDENING.md](/?tab=docsTab&doc=production-hardening)**.

## CLI equivalent

```bash
./ecosystem-stack/ecosystem-stack.sh start airllm
./ecosystem-stack/ecosystem-stack.sh stop ollama
./leco-cli.sh stack status
```

Back: [Dashboard tour](help:dash-overview) · Next: [Infrastructure tab](help:dash-infra)
