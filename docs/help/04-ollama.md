# Ollama (GGUF models)

**Container:** `ollama` · **URL:** `https://ollama.lh` · **API:** Ollama-compatible on port 11434.

## Dashboard — Model manager

**Infrastructure → 5 · Ollama → Model manager**

1. Choose **Popular ▾** (e.g. `llama3.2:3b`) or type a name like `qwen2.5:7b`.
2. Click **Install** to pull.
3. Click **Load** to keep the model in RAM for fast inference.
4. **Show CLI** for terminal commands without running them.

Curated list file (editable): `ecosystem-stack/config/popular-ollama-models.json`

Pinned auto-pull on stack start: `ecosystem-stack/config/ollama-pinned-models.txt`

## CLI (`leco-cli.sh`)

```bash
./leco-cli.sh ollama popular              # list curated models
./leco-cli.sh ollama install llama3.2:3b  # pull
./leco-cli.sh ollama load llama3.2:3b     # warm RAM
./leco-cli.sh ollama unload llama3.2:3b
./leco-cli.sh ollama remove-model llama3.2:3b
./leco-cli.sh ollama list
./leco-cli.sh ollama show-cmd llama3.2:3b
```

## API examples

```bash
curl -fsS -H "Host: ollama.lh" http://127.0.0.1/api/tags
curl -fsS -X POST -H "Host: ollama.lh" http://127.0.0.1/api/pull \
  -H 'Content-Type: application/json' \
  -d '{"name":"llama3.2:3b","stream":false}'
```

## Open WebUI

Open WebUI at `https://ai.lh` can use Ollama as a backend (`http://ollama:11434` from containers on `lh-network`).

Compare with AirLLM: [Ollama vs AirLLM](help:llm-compare)
