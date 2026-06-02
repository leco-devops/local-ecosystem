# Paperclip (AI agent orchestration)

**Container:** `paperclip` · **URL:** `https://paperclip.lh` · **Database:** `paperclip_postgres` (PostgreSQL 17)

[Paperclip](https://github.com/paperclipai/paperclip) is an open-source control plane for teams of AI agents — org charts, goals, budgets, heartbeats, and governance. It complements workflow tools like n8n by focusing on **multi-agent coordination** (Cursor, Claude Code, Codex, OpenClaw, HTTP bots).

## Start & verify

Paperclip is included in the **`ai-full`** and **`full`** install profiles. Start it from the dashboard **Control** tab or:

```bash
./ecosystem-stack/ecosystem-stack.sh start paperclip-postgres
./ecosystem-stack/ecosystem-stack.sh start paperclip
curl -kfsS https://paperclip.lh/
docker ps --filter name=paperclip
```

The service script pulls **`ghcr.io/paperclipai/paperclip:latest`** by default. Override with `PAPERCLIP_IMAGE` if needed.

## First visit

1. Open **https://paperclip.lh**
2. Create the **first board admin** using one of:
   - **Dashboard:** Infrastructure → **Paperclip** → **Bootstrap CEO invite** (streams live output and opens the invite URL)
   - **CLI:** `./leco-cli.sh paperclip bootstrap-ceo`
   - **Deploy CLI:** `leco-devops platform paperclip-bootstrap-ceo -E /path/to/local-ecosystem`
   - **Stack:** `./ecosystem-stack/ecosystem-stack.sh paperclip-bootstrap-ceo`
3. Open the printed invite link and complete signup
4. Define a company, hire agents, and assign goals

The bootstrap command runs inside the container from `/app` with data under `/paperclip`:

```bash
docker exec paperclip sh -c 'cd /app && pnpm paperclipai auth bootstrap-ceo -d /paperclip --base-url http://paperclip.lh'
```

If no config exists yet, the dashboard and deploy CLI run non-interactive `paperclipai onboard -y` first, then bootstrap CEO.

Telemetry is disabled in the LEco service script (`PAPERCLIP_TELEMETRY_DISABLED=1`, `DO_NOT_TRACK=1`).

## Database

| Setting | Value |
|---------|--------|
| Container | `paperclip_postgres` |
| User / password | `paperclip` / `paperclip` |
| Database | `paperclip` |
| From other containers | `postgresql://paperclip:paperclip@paperclip_postgres:5432/paperclip` |

Service hub: **http://localhost.lh/hub/paperclip-postgres**

## Connect local LLMs

Paperclip agents bring their own runtimes. Point coding agents at your LEco stack:

| Service | URL | Use for |
|---------|-----|---------|
| Ollama | `https://ollama.lh` | Local GGUF models |
| Open WebUI | `https://ai.lh` | Chat UI + model hub |
| AirLLM | `https://airllm.lh` | Large HuggingFace models |

Agents on the same Docker network can also reach backends directly (`http://ollama:11434`, etc.).

## Control & reset

| Action | CLI |
|--------|-----|
| Restart | `./ecosystem-stack/ecosystem-stack.sh restart paperclip` |
| Bootstrap CEO | `./ecosystem-stack/ecosystem-stack.sh paperclip-bootstrap-ceo` |
| Logs | `./ecosystem-stack/ecosystem-stack.sh logs paperclip` |
| Reset app data | Control → Paperclip → **Reset** (wipes `paperclip_data` volume) |
| Reset database | Control → PostgreSQL (Paperclip) → **Reset** (stops Paperclip first) |

## Traefik 404 on `paperclip.lh`

1. Containers running? `docker ps --filter name=paperclip`
2. On `lh-network`? `docker network inspect lh-network`
3. Route present? `./ecosystem-stack/ecosystem-stack.sh heal traefik`
4. DNS: add `paperclip.lh` to `/etc/hosts` (see [Install — DNS](help:install-dns))

See also: [Ecosystem stack (developer)](help:dev-ecosystem-stack) · [Control tab](help:dash-control)
