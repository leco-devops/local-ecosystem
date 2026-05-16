# Ecosystem updates (auto-generated)

_Generated at **2026-05-16T06:48:47Z** by `leco-update-catalog`. [Refresh service](/help?topic=ecosystem-updates)_

## Stack service versions

| Service | Status | Running | Latest | Upgrade |
|---------|--------|---------|--------|---------|
| Traefik | **not_running** | `—` | `traefik:v1.0.0` | Edit traefik/dynamic.yml in git if stack routes changed<br>./ecosystem-stack/ecosystem-stack.sh heal traefik |
| Ollama | **not_running** | `—` | `ollama/ollama:0.0.13` | ./ecosystem-stack/ecosystem-stack.sh restart ollama<br>Models persist in Docker volume ollama |
| AirLLM shim | **not_running** | `—` | `local-airllm:latest` | ./leco-cli.sh airllm build<br>./leco-cli.sh airllm start |
| Open WebUI | **not_running** | `—` | `ghcr.io/open-webui/open-webui:0.9.5` | ./ecosystem-stack/ecosystem-stack.sh restart webui |
| n8n | **not_running** | `—` | `n8nio/n8n:0.1.2` | ./ecosystem-stack/ecosystem-stack.sh restart n8n |
| PostgreSQL (n8n) | **not_running** | `—` | `postgres:9.1.16` | Back up n8n data before major Postgres upgrades<br>./ecosystem-stack/ecosystem-stack.sh restart postgres |
| LEco DevOps dashboard | **not_running** | `—` | `local/service-dashboard:latest` | ./ecosystem-stack/ecosystem-stack.sh restart dashboard<br>Or: bash ./ecosystem-stack/services/dashboard.sh deploy |

## New Ollama library entries

- `cogito-2.1:671b` — `./leco-cli.sh ollama install cogito-2.1:671b`
- `deepseek-v3.1:671b` — `./leco-cli.sh ollama install deepseek-v3.1:671b`
- `deepseek-v3.2` — `./leco-cli.sh ollama install deepseek-v3.2`
- `deepseek-v4-flash` — `./leco-cli.sh ollama install deepseek-v4-flash`
- `deepseek-v4-pro` — `./leco-cli.sh ollama install deepseek-v4-pro`
- `devstral-2:123b` — `./leco-cli.sh ollama install devstral-2:123b`
- `devstral-small-2:24b` — `./leco-cli.sh ollama install devstral-small-2:24b`
- `gemini-3-flash-preview` — `./leco-cli.sh ollama install gemini-3-flash-preview`
- `gemma3:12b` — `./leco-cli.sh ollama install gemma3:12b`
- `gemma3:27b` — `./leco-cli.sh ollama install gemma3:27b`
- `gemma3:4b` — `./leco-cli.sh ollama install gemma3:4b`
- `gemma4:31b` — `./leco-cli.sh ollama install gemma4:31b`
- `glm-4.6` — `./leco-cli.sh ollama install glm-4.6`
- `glm-4.7` — `./leco-cli.sh ollama install glm-4.7`
- `glm-5` — `./leco-cli.sh ollama install glm-5`
- `glm-5.1` — `./leco-cli.sh ollama install glm-5.1`
- `gpt-oss:120b` — `./leco-cli.sh ollama install gpt-oss:120b`
- `gpt-oss:20b` — `./leco-cli.sh ollama install gpt-oss:20b`
- `kimi-k2-thinking` — `./leco-cli.sh ollama install kimi-k2-thinking`
- `kimi-k2.5` — `./leco-cli.sh ollama install kimi-k2.5`

See [How to upgrade](help:deploy-rebuild).
