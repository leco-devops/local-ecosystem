# Ecosystem updates (auto-generated)

_Generated at **2026-08-20T15:22:17Z** by `leco-update-catalog`. [Refresh service](/help?topic=ecosystem-updates)_

## Stack service versions

| Service | Status | Running | Latest | Upgrade |
|---------|--------|---------|--------|---------|
| Traefik | **not_running** | `—` | `traefik:v1.0.0` | Edit traefik/dynamic.yml in git if stack routes changed<br>./ecosystem-stack/ecosystem-stack.sh heal traefik |
| Ollama | **not_running** | `—` | `ollama/ollama:0.0.13` | ./ecosystem-stack/ecosystem-stack.sh restart ollama<br>Models persist in Docker volume ollama |
| AirLLM shim | **not_running** | `—` | `local-airllm:latest` | ./leco-cli.sh airllm build<br>./leco-cli.sh airllm start |
| Open WebUI | **not_running** | `—` | `ghcr.io/open-webui/open-webui:0.11.0` | ./ecosystem-stack/ecosystem-stack.sh restart webui |
| n8n | **not_running** | `—` | `n8nio/n8n:0.1.2` | ./ecosystem-stack/ecosystem-stack.sh restart n8n |
| Paperclip | **not_running** | `—` | `ghcr.io/paperclipai/paperclip:2026.817.0` | ./ecosystem-stack/ecosystem-stack.sh restart paperclip<br>Data persists in Docker volumes paperclip_data and paperclip_postgres_data |
| PostgreSQL (Paperclip) | **not_running** | `—` | `postgres:9.1.17` | Back up Paperclip data before major Postgres upgrades<br>./ecosystem-stack/ecosystem-stack.sh restart paperclip-postgres |
| PostgreSQL (n8n) | **not_running** | `—` | `postgres:9.1.16` | Back up n8n data before major Postgres upgrades<br>./ecosystem-stack/ecosystem-stack.sh restart postgres |
| LEco DevOps dashboard | **not_running** | `—` | `local/service-dashboard:latest` | ./ecosystem-stack/ecosystem-stack.sh restart dashboard<br>Or: bash ./ecosystem-stack/services/dashboard.sh deploy |

## New Ollama library entries

- `deepseek-v4-flash:0731` — `./leco-cli.sh ollama install deepseek-v4-flash:0731`
- `deepseek-v4-flash:preview` — `./leco-cli.sh ollama install deepseek-v4-flash:preview`
- `deepseek-v4-pro:0813` — `./leco-cli.sh ollama install deepseek-v4-pro:0813`
- `deepseek-v4-pro:preview` — `./leco-cli.sh ollama install deepseek-v4-pro:preview`
- `gemma4:31b` — `./leco-cli.sh ollama install gemma4:31b`
- `glm-5.1` — `./leco-cli.sh ollama install glm-5.1`
- `glm-5.2` — `./leco-cli.sh ollama install glm-5.2`
- `gpt-oss:120b` — `./leco-cli.sh ollama install gpt-oss:120b`
- `gpt-oss:20b` — `./leco-cli.sh ollama install gpt-oss:20b`
- `kimi-k2.6` — `./leco-cli.sh ollama install kimi-k2.6`
- `kimi-k2.7-code` — `./leco-cli.sh ollama install kimi-k2.7-code`
- `kimi-k3` — `./leco-cli.sh ollama install kimi-k3`
- `minimax-m2.7` — `./leco-cli.sh ollama install minimax-m2.7`
- `minimax-m3` — `./leco-cli.sh ollama install minimax-m3`
- `mistral-large-3:675b` — `./leco-cli.sh ollama install mistral-large-3:675b`
- `nemotron-3-nano:30b` — `./leco-cli.sh ollama install nemotron-3-nano:30b`
- `nemotron-3-super` — `./leco-cli.sh ollama install nemotron-3-super`
- `nemotron-3-ultra` — `./leco-cli.sh ollama install nemotron-3-ultra`
- `qwen3.5:397b` — `./leco-cli.sh ollama install qwen3.5:397b`

See [How to upgrade](help:deploy-rebuild).
