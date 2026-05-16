# Infrastructure tab

**Infrastructure** is the operational home for stack health, metrics trends, Cloudflare local, **LLM model management**, and full Docker inventory.

## Jump bar (sections 1–8)

At the top of the tab, use the quick links:

**Health · Services · Trends · CF local · Ollama · AirLLM · AI · Inventory**

Click **Ollama** or **AirLLM** to scroll directly to the model manager.

## Section map

| # | Section | What you do here |
|---|---------|------------------|
| 1 | Platform health | Docker daemon, disk, aggregate status |
| 2 | Managed services | Cards per stack service (Traefik, Ollama, AirLLM, …) with CPU/RAM and URL probes |
| 3 | Utilization & trends | Historical charts |
| 4 | Cloudflare local | KV/R2/D1 adapter reachability |
| **5** | **Ollama** | **Model manager** — GGUF models |
| **6** | **AirLLM** | **Model manager** — HuggingFace large models |
| 7 | AI-assisted onboarding | Cloud/local provider for registration wizard |
| 8 | Docker inventory | Every container on the host |

## Model manager panel (Ollama & AirLLM)

Each LLM section has a highlighted **Model manager** card containing:

| Control | Action |
|---------|--------|
| **Popular ▾** | Pick a curated model; fills the text field |
| **Model** / **HF model** | Type any model id manually |
| **Install** | Pull/download into backend |
| **Load** | Warm into RAM (`keep_alive=-1`) |
| **Unload** | Free RAM (`keep_alive=0`) |
| **Remove** | Delete from disk (confirmed) |
| **Show CLI** | Copy-paste `leco-cli.sh` and `curl` commands |
| **Pull all pinned** | Uses `ecosystem-stack/config/*-pinned-models.txt` |
| **Refresh** | Reload model table |
| **Backup / Restore pinned** | Snapshot pinned list to `.local-eco-backups/` |

Below the toolbar, a **table** lists installed models with per-row actions (pin, inspect, load, delete).

### If you do not see the Model manager

1. **Hard-refresh** the browser (`Cmd+Shift+R`) — static JS is cache-busted but browsers may hold old `dashboard.js`.
2. **Restart dashboard** after git pull:
   ```bash
   ./ecosystem-stack/ecosystem-stack.sh restart dashboard
   ```
3. Confirm you are on **Infrastructure**, not only **Overview** service cards.
4. Scroll past sections 1–4 — Ollama is section **5**.

Next: [Ollama guide](help:ollama) · [AirLLM guide](help:airllm)
