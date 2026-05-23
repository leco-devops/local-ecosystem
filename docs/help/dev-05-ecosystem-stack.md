# Ecosystem stack (developer)

Shell orchestration under **`ecosystem-stack/`**.

## Entry points

| Script | Role |
|--------|------|
| `ecosystem-stack.sh` | CLI: `menu`, `start`, `stop`, `restart`, `deploy`, `heal traefik`, bulk actions |
| `core.sh` | `lh-network`, `START_ORDER`, `run_service`, `repair_network_links`, `bulk_ecosystem` |
| `leco-cli.sh` | User-friendly wrapper (stack, ollama, airllm, hosted apps menus) |

## Per-service scripts

`ecosystem-stack/services/*.sh` — each defines image build, volumes, env, network attach:

| Service | Script | Notes |
|---------|--------|-------|
| Traefik | `traefik.sh` | `ensure_hosting_files`, `heal` |
| Dashboard | `dashboard.sh` | `/project` mount, workspace parent |
| Ollama | `ollama.sh` | |
| AirLLM | `airllm.sh` | Custom Dockerfile in `ecosystem-stack/airllm/` |
| Open WebUI | `webui.sh` | |
| n8n / Postgres | `n8n.sh`, `postgres.sh` | |
| Cloudflare local | `cloudflare-local.sh` | |
| Infra add-ons | `infra.sh` | |
| File transfer (FTP, SFTP) | `file-transfer.sh` | |

## Network

**`lh-network`** — external Docker network. `core.sh` `repair_network_links` attaches stack containers after start.

Hosted app compose must attach to same network (hosting overlay).

## Adding a new stack service

1. Create **`ecosystem-stack/services/<name>.sh`**
2. Add to **`START_ORDER`** in `core.sh`
3. Add routers to **`traefik/dynamic.yml`**
4. Add to **`dashboard/monitor.py`** `SERVICE_MAP` (and overview cards if needed)
5. Document in `docs/DEPLOYMENT.md`
6. Run `heal traefik` after first deploy

## AirLLM-specific

- Image: `ecosystem-stack/airllm/Dockerfile`, `server.py`
- Route: `traefik/dynamic.yml` → `airllm-service` → `http://airllm:11435`
- Dashboard: `airllm_models.py`, `ai_provider.AirLLMProvider`

See user help [AirLLM](help:airllm) and `docs/AIRLLM_INTEGRATION.md`.

## Dashboard deploy

```bash
./ecosystem-stack/ecosystem-stack.sh restart dashboard
bash ./ecosystem-stack/services/dashboard.sh deploy
```

Env tuning: `DASHBOARD_SKIP_BUILD`, `DASHBOARD_SKIP_TRAEFIK_HEAL`, `DASHBOARD_HOST_SYS` (Linux host metrics).

Next: [Extending LEco](help:dev-extending)
