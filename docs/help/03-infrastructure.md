# Infrastructure tab

**Operate → Infrastructure** is the operational home for stack health, metric trends, Cloudflare local, **LLM model management**, agent orchestration, and the full Docker inventory.

## Jump bar

A sticky bar at the top of the tab:

**Health · Services · Trends · CF local · Ollama · AirLLM · AI · Inventory · Help manual**

Click **Ollama** or **AirLLM** to scroll straight to the model manager. **Help manual** leaves the tab and opens `/help`.

## Start stacks

Section 1 opens with four buttons that bring groups of containers up without going to Control:

**Start ecosystem** · **Start Cloudflare local** · **Start infra add-ons** · **Start FTP / SFTP**

These are the same operations as the Control tab's bulk bars, and honour the same [default policies](help:dash-control).

## Section map

| Section | What you do here |
|---------|------------------|
| **1 · Platform health** | Docker daemon reachability, disk, aggregate status, runtime summary, error rate |
| **2 · Managed services** | Cards per stack service with CPU/RAM and URL probes |
| **2b · File transfer (FTP / SFTP)** | SFTP, FTP, read-only browser (`files.lh`) — [guide](help:file-transfer) |
| **3 · Utilization & trends** | Historical charts |
| **4 · Cloudflare local** | KV / R2 / D1 adapter reachability |
| **5 · Ollama** | **Model manager** — GGUF models |
| **6 · AirLLM (Large Models)** | **Model manager** — HuggingFace large models |
| **7 · Paperclip (agent orchestration)** | Agent org charts, goals, budgets, governance — [Paperclip](help:paperclip) |
| **8 · AI-assisted onboarding** | Read-only status of the configured AI provider, plus a link to configure it |
| **Docker inventory** | Every container on the host, broader than the managed service map |

> The Docker inventory section is the last one on the page. Its heading currently reads `7 · Docker inventory` — the number is stale, the position is correct.

UI credentials and auto-login are **not** on this tab — they live on **Service hubs → UI access**.

## AI provider — configured on Service hubs, shown here

Section 8 is a **read-only mirror**. It shows the **Active provider** (name, model, and whether traffic is local, cloud, or mixed) and one button: **Configure on Service hubs →**.

The configuration screen itself is **Service hubs → AI providers (LLM access)** (`/hub#hub-ai-providers`):

1. **1 · Provider** — pick one of: *No AI (deterministic only)*, **Ollama (local)**, **AirLLM (local large models)**, **Anthropic (Claude API)**, **Google (Gemini API)**, **OpenAI**, **Aggregator / OpenAI-compatible**, or **Hybrid (local SLM + cloud LLM)**.
   *Eden AI, OpenRouter, Groq, Together, DeepSeek, Mistral, xAI, LiteLLM, vLLM, LM Studio and friends are **presets of “Aggregator / OpenAI-compatible”**, not separate providers — pick the aggregator, then pick the service from the **Service** dropdown and it resolves its own base URL.*
2. Fill only the fields that provider needs — **Service**, **Base URL**, **API key**, **Hybrid pair**, **Request timeout (seconds)**.
3. **Connect & list models** — LEco calls the provider and lists its **real** catalogue. Filter by name, sort by *Capability tier*, *Name*, *Context window*, or *Cost*.
4. Click a model to fill **Selected model id**, then **Save configuration**. It takes effect immediately; no dashboard restart.

**Do not type a model name from memory.** A provider paired with another vendor's model id (an Anthropic provider with `gpt-4o-mini`, for example) authenticates fine and then fails with **HTTP 404** on the first real call. That is exactly what the *Connect & list models* step exists to prevent — see [Common issues](help:ts-common).

### Where the key goes

Credentials are written server-side only, to **`config/ai-providers.yaml`** (mode `0600`, gitignored). They are never written into the repository, a manifest, `leco.app.yaml`, a log line, or any API response — the browser only ever receives a mask such as `sk-a••••••••••••x9f2`. The key field is **never prefilled**; leave it blank to keep the stored key, or use **Remove stored key** to delete it on the next save.

Saving needs the control token from the [Control tab](help:dash-control) when `DASHBOARD_CONTROL_TOKEN` is set.

The chosen provider drives **AI-assisted onboarding** in the Register wizard ([Onboarding overview](help:onboarding-overview)) and other AI-assisted LEco tasks.

### Ask LEco (retrieval over this repo and this machine) — API only, no UI yet

There is an internal question-answering API at **`/api/ai/rag/*`**. It retrieves from the repository's own documentation (`docs/**`, the top-level guides, and the Claude plugin skills) and can mix in **live state** from this machine — stack status, services, control targets, hosted apps, Traefik routes, Cloudflare local, the AI config with keys masked, and service or per-app logs — then answers with numbered citations back to the source files.

**There is no screen for it.** Nothing in the dashboard calls these endpoints today; the only way to use it is `curl` (or another HTTP client):

```bash
# What is indexed, how fresh it is, which provider would answer
curl -s http://localhost:8090/api/ai/rag/status

# Ask a question (add -H "X-Control-Token: $DASHBOARD_CONTROL_TOKEN" when a token is set)
curl -s http://localhost:8090/api/ai/rag/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"why is my app returning 502 through Traefik?","top_k":6}'

# Retrieval only — never contacts a provider, useful to check the corpus
curl -s http://localhost:8090/api/ai/rag/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"where are UI credentials stored","retrieval_only":true,"live":false}'
```

`POST /api/ai/rag/ask/stream` returns NDJSON (`status` → `sources` → `token`… → `done`); `POST /api/ai/rag/reindex` rebuilds the index and can opt into local embeddings. `GET /api/ai/rag/status` needs no token; the other three respect the control token.

The index rebuilds itself when a document's mtime changes, so editing a file under `docs/` is picked up without restarting anything.

## Model manager panel (Ollama & AirLLM)

Each LLM section has a highlighted **Model manager** card:

| Control | Action |
|---------|--------|
| **Popular ▾** | Pick a curated model; fills the text field |
| **Model** / **HF model** | Type any model id manually |
| **Install** | Pull/download into the backend |
| **Load** | Warm into RAM (`keep_alive=-1`) |
| **Unload** | Free RAM (`keep_alive=0`) |
| **Remove** | Delete from disk (confirmed) |
| **Show CLI** | Copy-paste `leco-cli.sh` and `curl` commands |
| **Pull all pinned** | Uses `ecosystem-stack/config/*-pinned-models.txt` |
| **Refresh** | Reload model table |
| **Backup / Restore pinned** | Snapshot the pinned list to `.local-eco-backups/` |

Below the toolbar, a table lists installed models with per-row actions.

### If you do not see the Model manager

1. **Hard-refresh** the browser (`Cmd+Shift+R`).
2. **Restart the dashboard** after a git pull:
   ```bash
   ./ecosystem-stack/ecosystem-stack.sh restart dashboard
   ```
3. Confirm you are on **Operate → Infrastructure**, not the Overview service cards.
4. Use the jump bar — Ollama is section **5**, AirLLM section **6**.

Next: [Ollama guide](help:ollama) · [AirLLM guide](help:airllm) · [Paperclip](help:paperclip) · [File transfer](help:file-transfer)
