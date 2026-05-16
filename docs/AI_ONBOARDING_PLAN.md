# AI-Assisted Onboarding — Architecture & Implementation Plan

## 1. Problem Statement

Onboarding a new application into LEco DevOps requires understanding the app's architecture — entry points, data stores, ports, config keys, health endpoints — then translating that understanding into 4–7 LEco config files. The current deterministic wizard detects file existence (compose, wrangler, archetype) but cannot read source code to infer behaviour. A developer still manually figures out what ports the app listens on, what config keys reference localhost, and how to wire the hosting overlay.

AI can bridge this gap: read the source, extract structured facts, and feed them into deterministic template generators.

---

## 2. Hybrid AI Provider Architecture

Users choose their AI backend. Three modes:

```
┌──────────────────────────────────────────────────────────────┐
│                    AI PROVIDER SELECTOR                       │
│                                                              │
│  ○  No AI (deterministic only)                              │
│  ○  Local AI — Ollama (private, no data leaves machine)     │
│  ○  Cloud AI — bring your own API key                       │
│      ┌──────────────────────────────────────────┐           │
│      │  Provider: [OpenAI ▾]                     │           │
│      │  Model:    [gpt-4o-mini ▾]               │           │
│      │  API Key:  [sk-•••••••••••••••] [Test ▶] │           │
│      └──────────────────────────────────────────┘           │
└──────────────────────────────────────────────────────────────┘
```

### 2.1 Supported Providers

| Provider | Models (suggested) | Context | Structured Output | Privacy | Cost |
|----------|--------------------|---------|-------------------|---------|------|
| **None** | — | — | — | Full | Free |
| **Ollama** (local) | qwen2.5-coder, deepseek-coder, llama3, deepseek-llm | 4K–32K | Fair | Full — nothing leaves machine | Free |
| **OpenAI** | gpt-4o-mini, gpt-4o | 128K | Excellent (JSON mode) | Code sent to OpenAI | ~$0.01–0.10/analysis |
| **Anthropic** | claude-sonnet-4-20250514, claude-haiku-4-20250414 | 200K | Excellent | Code sent to Anthropic | ~$0.01–0.05/analysis |
| **Google** | gemini-2.0-flash, gemini-2.5-pro | 1M | Good | Code sent to Google | ~$0.01–0.05/analysis |
| **OpenAI-compatible** | any | varies | varies | depends on host | varies |

The "OpenAI-compatible" option covers self-hosted endpoints (vLLM, LM Studio, text-generation-webui, LocalAI) that expose the OpenAI chat completions API at a custom base URL.

### 2.2 Provider Abstraction Layer

A single Python module (`ai_provider.py`) that normalises all providers behind one interface:

```python
class AIProvider(ABC):
    """Base class — every provider implements this."""

    @abstractmethod
    def analyze(self, system_prompt: str, user_prompt: str, *,
                stream: bool = False) -> AnalysisResult | Iterator[StreamChunk]:
        """Send analysis prompt, return structured JSON or stream tokens."""

    @abstractmethod
    def health_check(self) -> ProviderStatus:
        """Check connectivity and model availability."""

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Return available models for this provider."""


class OllamaProvider(AIProvider):       # POST http://ollama:11434/api/generate
class OpenAIProvider(AIProvider):       # POST https://api.openai.com/v1/chat/completions
class AnthropicProvider(AIProvider):    # POST https://api.anthropic.com/v1/messages
class GoogleProvider(AIProvider):       # POST https://generativelanguage.googleapis.com/...
class OpenAICompatibleProvider(AIProvider):  # custom base_url + OpenAI SDK format
```

Each provider handles its own authentication (API key header), error mapping, rate limit retries, and JSON extraction from model output. The orchestrator never sees provider-specific details.

### 2.3 Why the Abstraction Matters

- **Cloud models are dramatically better at structured output.** GPT-4o with `response_format: { type: "json_object" }` almost never produces malformed JSON. A local 7B model might need regex-based extraction and retry. The provider layer hides this.
- **Context window differences.** With Ollama we budget ~12K tokens and carefully truncate files. With Claude or Gemini we can send 50K+ and get richer analysis. The file collector adapts its budget based on the provider's context window.
- **Streaming behaviour.** Ollama streams via `POST /api/generate` with NDJSON. OpenAI streams via SSE. Anthropic streams via SSE with different event types. The provider normalises all of these into a common `Iterator[StreamChunk]`.

---

## 3. API Key Management

### 3.1 Storage: Server-Side Config File

API keys are stored in a YAML file on the server, **not** in browser localStorage and **not** in environment variables:

```
config/ai-providers.yaml          ← gitignored, chmod 600
```

```yaml
# AI provider configuration for LEco DevOps onboarding
# This file is gitignored. Do not commit API keys.

default_provider: ollama           # "none" | "ollama" | "openai" | "anthropic" | "google" | "openai-compatible"
default_model: qwen2.5-coder      # model name (provider-specific)

providers:
  ollama:
    base_url: http://ollama:11434  # default; override if Ollama runs elsewhere
    default_model: qwen2.5-coder

  openai:
    api_key: ""                    # sk-...
    default_model: gpt-4o-mini

  anthropic:
    api_key: ""                    # sk-ant-...
    default_model: claude-sonnet-4-20250514

  google:
    api_key: ""                    # AIza...
    default_model: gemini-2.0-flash

  openai-compatible:
    base_url: ""                   # e.g. http://localhost:1234/v1
    api_key: ""                    # optional
    default_model: ""
```

### 3.2 Why Not Environment Variables

The dashboard already has `DASHBOARD_CONTROL_TOKEN` as an env var. But AI keys are different:

- Users may want to change providers **without restarting the dashboard container**
- Multiple keys for different providers
- A settings UI should be able to write keys — env vars are read-only at runtime

A config file read on each request (with caching) solves all three.

### 3.3 Why Not Browser localStorage

API keys in localStorage means they're visible in browser DevTools, included in any XSS payload, and lost when the user clears browser data. Server-side storage with the control token protecting the settings API is safer.

### 3.4 Security

- `ai-providers.yaml` is gitignored (add to `.gitignore`)
- File permissions set to `600` (owner read/write only)
- Settings API endpoints require control token
- API keys are masked in UI (show last 4 chars only)
- Keys are never sent to the browser — only the provider name and model list
- A `[Test Connection]` button validates the key server-side and returns pass/fail

### 3.5 Privacy Warning in UI

When a user selects a cloud provider, the UI shows a clear warning:

```
⚠️  Cloud AI: source code excerpts (~8-12 files, truncated) will be sent to
    OpenAI's API for analysis. No files are stored by the provider beyond the
    API request. Use "Local AI (Ollama)" if code must stay on your machine.
```

---

## 4. Dashboard Settings Panel

A new "AI Settings" section in the **Infrastructure** tab (next to the existing Ollama panel):

```
┌─────────────────────────────────────────────────────────────────┐
│  AI-Assisted Onboarding                                         │
│                                                                 │
│  Default Provider: [Ollama (local) ▾]                          │
│                                                                 │
│  ┌─ Ollama (local) ──────────────────────────────────────────┐ │
│  │  Status: ● Connected (3 models loaded)                     │ │
│  │  Base URL: http://ollama:11434                             │ │
│  │  Model: [qwen2.5-coder ▾]                                │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ OpenAI ──────────────────────────────────────────────────┐ │
│  │  API Key: [sk-••••••••••abcd    ] [Test ▶] ● Valid        │ │
│  │  Model: [gpt-4o-mini ▾]                                   │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ Anthropic ───────────────────────────────────────────────┐ │
│  │  API Key: [sk-ant-••••••efgh    ] [Test ▶] ○ Not set      │ │
│  │  Model: [claude-sonnet-4-20250514 ▾]                               │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ Google ──────────────────────────────────────────────────┐ │
│  │  API Key: [                     ] [Test ▶] ○ Not set      │ │
│  │  Model: [gemini-2.0-flash ▾]                              │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ OpenAI-Compatible ───────────────────────────────────────┐ │
│  │  Base URL: [http://localhost:1234/v1]                      │ │
│  │  API Key: [optional             ] [Test ▶] ○ Not tested   │ │
│  │  Model: [                       ]                          │ │
│  └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  [Save Settings]                                                │
└─────────────────────────────────────────────────────────────────┘
```

### Settings API Endpoints

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `GET /api/ai/settings` | GET | Return current config (keys masked) | Control token |
| `POST /api/ai/settings` | POST | Save provider config + API keys | Control token |
| `POST /api/ai/test` | POST | Test connection to a specific provider | Control token |
| `GET /api/ai/models` | GET | List available models for current provider | No |

---

## 5. The AI Analysis Pipeline (3 Phases)

Same 3-phase architecture as before, now provider-aware:

### Phase 1: COLLECT (no AI — pure Python)

Smart file collector reads the app directory with a **token budget** that adapts to the provider:

| Provider | Token Budget | Rationale |
|----------|-------------|-----------|
| Ollama (7B–13B) | ~12K tokens | 4K–32K context, leave room for system prompt + output |
| OpenAI (gpt-4o-mini) | ~30K tokens | 128K context, generous but not wasteful |
| Anthropic (Claude) | ~50K tokens | 200K context, can include more files |
| Google (Gemini Flash) | ~50K tokens | 1M context, but diminishing returns |
| None | 0 | No collection needed |

**File priority (same across all providers — budget determines how many):**

```
Priority 1 — always include (identity files):
  package.json             — dependencies, scripts, name
  config.js / config.ts    — the config object (key target for preloader)
  .env.example / .env.sample — expected environment variables
  docker-compose*.yml      — existing service definitions
  Dockerfile               — base image, ports, entry command

Priority 2 — include if budget allows (behaviour files):
  server.js / app.js / index.js   — first 100 lines (entry point, port binding)
  worker.js, cron.js, etc.        — first 40 lines (detect additional processes)
  nginx.conf / varnish VCL        — existing production configs
  routes/ or router files          — first 60 lines (detect health endpoints)

Priority 3 — include if generous budget (context files):
  README.md                — first 80 lines (architecture overview)
  Makefile / Procfile       — process definitions
  pm2.config.js            — PM2 process list
  package-lock.json         — SKIP (too large, no value)
  node_modules/             — SKIP always
```

Each file is **truncated** to its cap, and a `# [truncated at N lines]` marker is appended. Binary files are skipped.

**Output:** `CollectedContext` dict with file contents + metadata (total tokens, file count, budget used).

### Phase 2: ANALYZE (single AI call — structured JSON output)

The system prompt explains LEco architecture patterns and asks for a specific JSON schema. The user prompt includes the collected file contents.

**System prompt structure:**

```
You are analyzing a software application to generate Docker hosting configuration.

CONTEXT: LEco DevOps is a local development platform that runs apps via Docker Compose
with Traefik reverse proxy on *.lh hostnames.

PATTERNS YOU MUST DETECT:
1. Entry scripts — which JS/Python/etc. files start processes (server, worker, cron, queue)
2. Data stores — MongoDB, Redis, PostgreSQL, MySQL, Elasticsearch, etc.
3. Cache/proxy layer — Varnish, Nginx, HAProxy
4. Config file — the file that exports connection URIs, ports, hostnames
5. Config keys to patch — keys in the config that reference localhost/127.0.0.1
6. Health endpoint — the HTTP path used for health checks
7. Listening port — what port the main HTTP process binds to
8. Environment variables — what env vars the app reads

OUTPUT FORMAT: Return valid JSON matching this schema:
{ ... schema ... }

REFERENCE: Here is a working example analysis for a Node.js app called "botfeed":
{ ... botfeed example ... }
```

**Provider-specific behavior:**

| Provider | JSON Strategy | Fallback |
|----------|--------------|----------|
| OpenAI | `response_format: { type: "json_object" }` | Regex extraction |
| Anthropic | Prompt instructs JSON in `<json>` tags | Parse from tags |
| Google | `response_mime_type: "application/json"` | Regex extraction |
| Ollama | `format: "json"` parameter | Regex extraction + retry once |
| None | Skip — return empty analysis | Deterministic defaults |

**Fallback chain:** If the AI call fails (timeout, malformed output, provider down), the system falls back to deterministic generation with a yellow warning: "AI analysis failed — using file-detection defaults. You may need to edit the generated configs manually."

### Phase 3: GENERATE (no AI — deterministic Python templates)

Takes the structured JSON from Phase 2 and produces config files using Python template logic (not raw AI output). This guarantees valid syntax regardless of AI quality.

**Generated files from analysis JSON:**

| File | Generated From |
|------|---------------|
| `leco.yaml` | `listening_port`, `health_endpoint`, `cache_layer`, app slug |
| `leco.app.yaml` | `config_file`, app slug, standard bridge template |
| `docker-compose.yml` | `services[]`, `data_stores[]`, `cache_layer`, source path |
| `docker-compose.leco-hosting.yml` | `config_keys_to_patch`, `entry_scripts[]`, `environment_vars[]` |
| `leco-docker-preload.js` | `config_keys_to_patch` map (key → localhost value → Docker service name) |
| `conf/varnish/default.vcl` | Only if `cache_layer == "varnish"` — adapted from sample VCL template |

**Why templates, not raw AI file generation:**

Asking a 7B model (or even GPT-4o) to produce valid multi-file YAML + VCL + JavaScript in one shot is unreliable. But asking "what port does this app listen on?" and "what config keys reference localhost?" — that's an extraction task where even small models perform well. Deterministic templates guarantee valid syntax; the AI fills in the right values.

---

## 6. Registration Wizard Integration

The existing 5-step wizard flow, with AI enhancement at Step 2:

```
Step 1: DETECT (unchanged — file-existence scan)
         Returns: archetype, compose files, wrangler status
   ↓
Step 2: GENERATE CONFIGURATION
   ┌──────────────────────────────────────────────────────────┐
   │                                                          │
   │  AI Provider: [Ollama (local) ▾]  Model: [qwen2.5-coder ▾]  │
   │                                                          │
   │  [🤖 Analyze & Generate]     [Generate basic (no AI)]   │
   │                                                          │
   │  ┌─ Analysis Progress ────────────────────────────────┐ │
   │  │ ✓ Collected 11 files (8.2K tokens)                  │ │
   │  │ ✓ Detected: 4 Node.js processes, MongoDB, Redis,   │ │
   │  │   Varnish, health: /alb-health-check, port 3000    │ │
   │  │ ✓ Generated 6 config files                          │ │
   │  └────────────────────────────────────────────────────┘ │
   │                                                          │
   │  ┌─ Files ────────────────────────────────────────────┐ │
   │  │ [leco.yaml] [leco.app.yaml] [docker-compose.yml]   │ │
   │  │ [hosting overlay] [preloader.js] [varnish VCL]     │ │
   │  │ ┌──────────────────────────────────────────────┐   │ │
   │  │ │ <editable textarea with generated content>   │   │ │
   │  │ │                                              │   │ │
   │  │ └──────────────────────────────────────────────┘   │ │
   │  └────────────────────────────────────────────────────┘ │
   └──────────────────────────────────────────────────────────┘
   ↓
Step 3: SAVE (write files to disk — unchanged)
   ↓
Step 4: VALIDATE (schema check — unchanged, now validates more files)
   ↓
Step 5: REGISTER (ecosystem-register + optional deploy — unchanged)
```

The AI toggle **remembers** the user's last selection via the `default_provider` in `ai-providers.yaml`. If Ollama is down and no cloud key is configured, the toggle defaults to "No AI" with a note.

---

## 7. Streaming API

```
POST /api/leco/ai-analyze/stream
  Body: {
    app_root: "/path/to/app",
    app_id: "botfeed",
    provider: "ollama",          // override default (optional)
    model: "qwen2.5-coder",     // override default (optional)
    control_token: "..."
  }
  Returns: NDJSON stream (Content-Type: application/x-ndjson)

  Events:
    {"type":"phase","phase":"collect","text":"Collecting source files..."}
    {"type":"file","name":"config.js","lines":45,"tokens":820}
    {"type":"file","name":"server.js","lines":80,"tokens":1200}
    {"type":"file","name":"package.json","lines":32,"tokens":540}
    {"type":"phase","phase":"analyze","text":"Analyzing with qwen2.5-coder (Ollama)...","provider":"ollama","model":"qwen2.5-coder"}
    {"type":"token","text":"..."}                    // streamed LLM tokens (optional, for UX)
    {"type":"analysis","data":{"services":[...],...}} // parsed JSON from AI
    {"type":"phase","phase":"generate","text":"Generating 6 config files..."}
    {"type":"result","files":{
      "leco.yaml": "schemaVersion: 2\n...",
      "leco.app.yaml": "lecoAppVersion: '3'\n...",
      "docker-compose.yml": "services:\n...",
      "docker-compose.leco-hosting.yml": "...",
      "leco-docker-preload.js": "// Runtime config patcher\n...",
      "conf/varnish/default.vcl": "vcl 4.1;\n..."
    }}
    {"type":"done","ok":true}

  Error events:
    {"type":"error","phase":"analyze","text":"Ollama connection refused","fallback":"deterministic"}
    {"type":"warning","text":"AI output was malformed — using regex extraction (partial)"}
```

---

## 8. New Files & Changes

### New Files

```
dashboard/
├── ai_provider.py             — Provider abstraction (ABC + 5 implementations)
│     AIProvider, OllamaProvider, OpenAIProvider, AnthropicProvider,
│     GoogleProvider, OpenAICompatibleProvider
│     ProviderStatus, ModelInfo, AnalysisResult, StreamChunk
│
├── ai_config.py               — Read/write config/ai-providers.yaml
│     load_ai_config(), save_ai_config(), mask_key(),
│     get_active_provider() → AIProvider instance
│
├── ai_file_collector.py       — Smart file reader with token budget
│     collect_app_context(app_root, token_budget) → CollectedContext
│     FILE_PRIORITIES, SKIP_PATTERNS, estimate_tokens()
│
├── ai_prompts.py              — System prompt + few-shot examples
│     SYSTEM_PROMPT, build_analysis_prompt(), EXAMPLE_ANALYSIS,
│     ANALYSIS_JSON_SCHEMA
│
├── ai_template_generator.py   — Deterministic file generation from analysis JSON
│     generate_from_analysis(analysis, slug, source_path) → dict[str, str]
│     Templates for: leco.yaml, leco.app.yaml, docker-compose.yml,
│     hosting overlay, preloader.js, VCL
│
├── ai_orchestrator.py         — Ties collect → analyze → generate
│     run_ai_analysis(app_root, app_id, provider, model) → Iterator[StreamEvent]
│     Handles fallback chain, error recovery, progress events

config/
├── ai-providers.yaml          — API keys + provider defaults (gitignored)
```

### Modified Files

```
dashboard/app.py
  + GET  /api/ai/settings          — return config (keys masked)
  + POST /api/ai/settings          — save provider config
  + POST /api/ai/test              — test provider connection
  + GET  /api/ai/models            — list models for a provider
  + POST /api/leco/ai-analyze/stream — main analysis endpoint (NDJSON)

dashboard/templates/index.html
  + AI Settings section in Infrastructure tab
  + AI toggle + provider/model selectors in registration Step 2
  + Tabbed file preview area for generated configs

dashboard/static/dashboard.js
  + AI settings panel logic (save, test, model dropdown refresh)
  + Registration wizard AI integration (stream consumer, tab UI)
  + Provider/model memory (reads from server config, not localStorage)

.gitignore
  + config/ai-providers.yaml

dashboard/requirements.txt
  + openai>=1.0     (also covers OpenAI-compatible endpoints)
  + anthropic>=0.30
  + google-genai>=1.0    (lightweight — only chat completions)
  (Ollama needs no library — plain requests to HTTP API)
```

---

## 9. Safeguards & Edge Cases

| Risk | Mitigation |
|------|-----------|
| AI returns malformed JSON | Regex extraction fallback → deterministic fallback → yellow warning |
| Ollama container down | Health check greys out Ollama option; auto-selects next available or "None" |
| Cloud API key invalid / expired | `[Test]` button before analysis; clear error message with re-configure link |
| Cloud API rate limited | Exponential backoff (1 retry); then fallback to deterministic |
| App source tree too large (monorepo) | Token budget hard-cap; only read files in immediate app dir (max 2 levels deep) |
| Sensitive data in source files | Privacy warning on cloud providers; `.env` files with real secrets are **not** collected (only `.env.example` / `.env.sample`) |
| Model hallucinates service names | All AI output is structured JSON → deterministic templates use only known patterns. If a service name doesn't match a known type, it's logged but not used |
| Generated YAML invalid | Existing `validate-yaml` endpoint runs on every generated file before showing to user |
| User edits generated files incorrectly | Step 4 (Validate) catches schema violations before Step 5 (Register) |
| Multiple providers configured, default unclear | `default_provider` in config file; UI shows which is active with ● indicator |
| No internet (cloud providers unreachable) | Connection test fails gracefully; Ollama and "None" always available offline |

---

## 10. Implementation Order

| Phase | What | Files | Effort | Dependency |
|-------|------|-------|--------|------------|
| **1** | Provider abstraction + Ollama impl | `ai_provider.py` (Ollama only) | Medium | None |
| **2** | Config file management | `ai_config.py` + `ai-providers.yaml` template | Small | None |
| **3** | Smart file collector | `ai_file_collector.py` | Small | None |
| **4** | Prompt engineering + analysis schema | `ai_prompts.py` | Medium | Phase 1, 3 |
| **5** | Template generators from analysis JSON | `ai_template_generator.py` | Medium | Phase 4 |
| **6** | Orchestrator + streaming endpoint | `ai_orchestrator.py` + `app.py` routes | Medium | Phase 1–5 |
| **7** | Settings UI (Infrastructure tab) | `index.html` + `dashboard.js` | Medium | Phase 2, 6 |
| **8** | Registration wizard AI integration | `index.html` + `dashboard.js` | Medium | Phase 6, 7 |
| **9** | Cloud providers (OpenAI, Anthropic, Google) | `ai_provider.py` additions | Medium | Phase 1 |
| **10** | OpenAI-compatible provider | `ai_provider.py` addition | Small | Phase 9 |
| **11** | End-to-end test with botfeed source | Manual verification | Small | All |

**Phases 1–3 can run in parallel.** Phase 9–10 can be deferred — ship with Ollama-only first, add cloud providers in a follow-up.

---

## 11. CLI Integration

The `leco-devops` CLI also gets AI support via a `--ai` flag on existing commands:

```bash
# Scaffold with AI analysis (reads source, generates smarter configs):
leco-devops scaffold botfeed -E /path/to/local-ecosystem \
  --source-path /Users/rmaurya/Working/GitHub/UtilityServer \
  --ai                           # uses default provider from ai-providers.yaml
  --ai-provider openai           # override provider
  --ai-model gpt-4o-mini         # override model

# Init wizard with AI detection:
leco-devops init --ai               # AI enhances detection in the wizard

# Analyse only (no file generation — just print analysis JSON):
leco-devops ai-analyze /path/to/app --provider ollama --model qwen2.5-coder
```

The CLI reads `config/ai-providers.yaml` for keys and defaults, same as the dashboard. The `ai-analyze` command is useful for debugging prompts and validating AI output without generating files.

---

## 12. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DASHBOARD UI (Browser)                        │
│                                                                      │
│  ┌─ Infrastructure Tab ──────┐   ┌─ Registration Wizard ──────────┐ │
│  │ AI Settings Panel         │   │ Step 2: Generate Configuration │ │
│  │  • Provider selector      │   │  • Provider/model picker       │ │
│  │  • API key inputs         │   │  • [Analyze & Generate] button │ │
│  │  • [Test] buttons         │   │  • Streaming progress log      │ │
│  │  • [Save Settings]        │   │  • Tabbed file preview         │ │
│  └───────────────────────────┘   └────────────────────────────────┘ │
└──────────────────────────┬──────────────────────┬───────────────────┘
                           │                      │
              POST /api/ai/settings    POST /api/leco/ai-analyze/stream
                           │                      │
┌──────────────────────────▼──────────────────────▼───────────────────┐
│                     DASHBOARD BACKEND (Flask)                        │
│                                                                      │
│  ┌─────────────┐  ┌──────────────────────────────────────────────┐  │
│  │ ai_config   │  │ ai_orchestrator                               │  │
│  │  • load     │  │  Phase 1: ai_file_collector.collect()        │  │
│  │  • save     │  │  Phase 2: ai_provider.analyze()              │  │
│  │  • mask     │  │  Phase 3: ai_template_generator.generate()   │  │
│  └──────┬──────┘  └──────────┬─────────────────┬─────────────────┘  │
│         │                    │                 │                     │
│  ┌──────▼──────┐   ┌────────▼────────┐  ┌────▼──────────────────┐  │
│  │ai-providers │   │  ai_provider    │  │ai_template_generator │  │
│  │   .yaml     │   │  (abstraction)  │  │  (deterministic)     │  │
│  └─────────────┘   └───┬───┬───┬───┬─┘  └──────────────────────┘  │
│                         │   │   │   │                               │
└─────────────────────────┼───┼───┼───┼───────────────────────────────┘
                          │   │   │   │
          ┌───────────────┘   │   │   └──────────────────┐
          ▼                   ▼   ▼                      ▼
   ┌────────────┐  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐
   │   Ollama   │  │ OpenAI   │  │  Anthropic   │  │Google / OAI-compat│
   │  (Docker)  │  │  (API)   │  │   (API)      │  │    (API)          │
   │ :11434     │  │ cloud    │  │   cloud      │  │  cloud / local    │
   │ FREE       │  │ $0.01/q  │  │   $0.01/q    │  │  varies           │
   │ PRIVATE    │  │ sends    │  │   sends      │  │  depends           │
   │            │  │ code     │  │   code       │  │                    │
   └────────────┘  └──────────┘  └──────────────┘  └──────────────────┘
```
