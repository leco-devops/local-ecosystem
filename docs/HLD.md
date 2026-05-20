# LEco DevOps Open Project - HLD

> **Open source** · [MIT License](../LICENSE) · Maintained by [Techtonic Systems Media And Research LLC](https://techtonic.systems/)

This High-Level Design (HLD) defines major components, boundaries, and data/control flows.

## 1) Objectives

- Run a full local platform behind `*.lh` with consistent routing.
- Provide a single operations surface (LEco DevOps UI).
- Support external app onboarding via LEco manifests and registry.
- Keep hosted app lifecycle, routing, and local resource provisioning consistent.

## 2) Architectural layers

| Layer | Responsibility | Key artifacts |
| ----- | ----- | ----- |
| Edge | HTTP/S routing, TLS, service exposure | `traefik/dynamic.yml` (git) + **`hosting/traefik/`** loaded by Traefik (`01-stack-core.yml` copy + `dynamic.yml` merge) |
| Orchestration | Start/stop/restart/bulk operations | `ecosystem-stack/ecosystem-stack.sh`, `ecosystem-stack/core.sh`, `ecosystem-stack/services/*.sh` |
| Operations UI/API | Monitoring, control, docs, hosted app UX | `dashboard/` |
| LEco Toolchain | Manifest detection, register/unregister, deploy flows | `tools/deploy-cli/leco_app/` |
| Hosting materialization | Writable hosted app layout and symlink strategy | `hosting/`, `dashboard/hosting_layout.py` |
| Resource adapters | Optional local Cloudflare-style services | `cloudflare-local/` |

## 3) High-level flow

```mermaid
flowchart LR
  User["Operator"]
  Ui["LEco DevOps UI"]
  Control["Control API"]
  Cli["leco-devops CLI"]
  Registry["leco-registry.yaml"]
  Traefik["hosting/traefik/*.yml"]
  Docker["Docker engine"]
  Hosted["hosting/app-available"]

  User --> Ui
  Ui --> Control
  Control --> Docker
  Ui --> Cli
  Cli --> Registry
  Cli --> Traefik
  Cli --> Hosted
  Ui --> Registry
```

## 4) Core use cases

### A. Stack operations

- Operator triggers action from LEco DevOps Control tab.
- `dashboard/control.py` validates target/action, executes shell/compose/CLI flow.
- Status/stream updates are returned to UI.

### B. Hosted app registration

- Operator scans app root (`/api/leco/detect`).
- YAML is generated/saved (`/api/leco/generate-yaml`, `/api/leco/save-yaml`).
- Registration runs CLI (`/api/leco/register`), updates registry and optional Traefik routes.

### C. Hosted app offboarding

- Operator removes/reset app in Hosted apps.
- Offboard path uses `ecosystem-unregister`, local resource cleanup, route cleanup, and registry removal.

### D. AI-assisted onboarding

- Operator toggles "AI Assist" in the registration wizard or AI Settings panel.
- Provider selection: **No AI** (deterministic), **Ollama** (local SLM), **OpenAI / Anthropic / Google / OpenAI-Compatible** (cloud), **Hybrid** (local SLM + cloud LLM).
- **Hybrid mode**: Local SLM (e.g. Ollama/qwen2.5-coder) pre-summarizes source files (fast, free, private) → Cloud LLM (e.g. OpenAI/gpt-4o-mini) analyzes the condensed summary (accurate, ~3-5x fewer tokens = lower cost). Combines speed, accuracy, and cost efficiency.
- 3-phase pipeline: **Collect** (smart file reading within token budget) → **Analyze** (single or two-stage LLM call, structured JSON) → **Generate** (deterministic Python templates).
- AI never writes raw text to disk — it produces structured JSON that drives Python template generators.
- Config files produced: `leco.yaml`, `leco.app.yaml`, `docker-compose.yml`, `docker-compose.leco-hosting.yml`, `leco-docker-preload.js`, `conf/varnish/default.vcl`.
- API keys stored server-side in `config/ai-providers.yaml` (gitignored, chmod 600). Browser only sees masked keys.
- Streaming NDJSON to dashboard mirrors existing registration stream pattern.

```mermaid
flowchart LR
  Wizard["Registration Wizard"]
  AiAPI["AI Endpoints"]
  Collect["File Collector"]
  SLM["Local SLM (Ollama)"]
  LLM["Cloud LLM (OpenAI/etc)"]
  Gen["Template Generator"]
  Disk["App Directory"]

  Wizard -->|toggle AI| AiAPI
  AiAPI --> Collect
  Collect -->|token-budgeted files| SLM
  SLM -->|condensed summary| LLM
  LLM -->|structured JSON| Gen
  Gen -->|deterministic configs| Disk
```

## 5) Non-functional goals

- Deterministic local behavior and path handling (`/project`, `workspace-parent`, hosted materialization).
- Safe defaults for destructive operations (token-gated mutations).
- Clear docs and discoverability in both repo docs and in-app Docs tab.

## 6) Interface boundaries

- UI/Backend: Flask + static JS APIs under `/api/*`.
- Backend/CLI: subprocess wrapper in `dashboard/leco_subprocess.py`.
- Backend/Docker: socket and compose invocations.
- CLI/Manifests: `leco.app.yaml` bridge + `leco.yaml` profile model.

## 7) Risks and mitigation

- Routing drift: keep Traefik fragment generation centralized in CLI.
- Path drift: standardize on `hosting/app-available` and registry-relative manifests.
- Destructive actions: require `DASHBOARD_CONTROL_TOKEN` in sensitive environments.
- AI hallucination: AI produces structured data only; deterministic templates generate all config files. All output is human-reviewable before write.
- API key leakage: keys stored server-side (yaml, chmod 600, gitignored), never sent to browser. Masked display only.
- Token budget overrun: adaptive budgets per provider (12K local, 30-50K cloud) with priority-tiered file collection.
