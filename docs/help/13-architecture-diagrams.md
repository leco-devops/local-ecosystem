# Architecture & diagrams

Visual maps of the LEco DevOps platform. Diagrams render automatically on this page (Mermaid). If a diagram does not appear, hard-refresh the browser.

## Platform stack

```mermaid
flowchart TB
  subgraph Browser["Your machine"]
    U[Browser *.lh]
  end
  subgraph Edge["Docker · lh-network"]
    T[Traefik edge]
    D[LEco Dashboard<br/>localhost.lh]
    O[Ollama · ollama.lh]
    A[AirLLM · airllm.lh]
    W[Open WebUI · ai.lh]
    N[n8n · n8n.lh]
    CF[Cloudflare-local<br/>kv / r2 / d1 .lh]
    H[Hosted apps<br/>myapp.lh]
  end
  U -->|HTTPS Host header| T
  T --> D
  T --> O
  T --> A
  T --> W
  T --> N
  T --> CF
  T --> H
```

## Repository layout

```mermaid
flowchart LR
  subgraph Repo["local-ecosystem repo"]
    ES[ecosystem-stack/]
    DB[dashboard/]
    CLI[tools/deploy-cli/]
    TR[traefik/dynamic.yml]
    HO[hosting/app-available/]
    HT[hosting/traefik/]
    RG[config/leco-registry.yaml]
  end
  ES -->|starts| T2[Traefik + services]
  DB -->|/project mount| T2
  CLI -->|leco-devops| RG
  CLI -->|merge routes| HT
  HO -->|leco.app.yaml| RG
  TR -->|copy on start| HT
```

## Hosting slot (materialized app)

```mermaid
flowchart TB
  subgraph Slot["hosting/app-available/myapp/"]
    B[leco.app.yaml<br/>bridge · root: source]
    P[leco.yaml<br/>profile · infrastructure]
    S[source symlink]
    OV[docker-compose.leco-hosting.yml]
    RT[docker-compose.leco-runtime.yml]
    DV[.dev.vars]
  end
  subgraph Upstream["Sibling repo read-only wsp:"]
    APP[Real app tree<br/>compose · wrangler · src]
  end
  B --> P
  S --> APP
  P --> OV
  P --> RT
  B --> S
```

## Onboarding & registration data flow

```mermaid
sequenceDiagram
  participant UI as Dashboard UI
  participant API as Flask app.py
  participant Det as leco_detect
  participant Mat as leco_materialize
  participant Reg as leco_registration
  participant CLI as leco-devops
  participant Rg as leco-registry.yaml
  participant Tr as hosting/traefik/dynamic.yml

  UI->>API: POST /api/leco/detect
  API->>Det: scan path
  UI->>API: POST generate-yaml / save-yaml
  API->>Mat: materialize app-available/slug
  UI->>API: POST /api/leco/register
  API->>Reg: overlays + validate
  Reg->>CLI: ecosystem-register --merge-traefik
  CLI->>Rg: append app row
  CLI->>Tr: merge routing.entries
  opt Deploy stack
    Reg->>CLI: deploy
    CLI->>CLI: docker compose up -d --build
  end
```

## Traefik routing (two files)

```mermaid
flowchart LR
  subgraph Git["Git canonical"]
    TD[traefik/dynamic.yml<br/>stack routes]
  end
  subgraph Runtime["hosting/traefik/ watched by Traefik"]
    C[01-stack-core.yml<br/>copy of stack routes]
    DY[dynamic.yml<br/>per-app routes]
  end
  TD -->|Traefik start / heal| C
  REG[leco-devops register] -->|merge| DY
  C --> T[Traefik file provider]
  DY --> T
  T --> SVC[Containers on lh-network]
```

## Overriding upstream (three layers)

```mermaid
flowchart TB
  subgraph Up["Upstream repo unchanged"]
    UC[docker-compose.yml]
    UW[wrangler.toml]
  end
  subgraph LEco["Hosting slot only"]
    L1[additionalComposeFilesFromManifest<br/>lh-network · ports reset]
    L2[composeFileFromManifest<br/>include upstream compose]
    L3[infrastructure.runtimes<br/>local Worker container]
  end
  UC -.->|include| L2
  L1 --> Compose[docker compose -f chain]
  L2 --> Compose
  L3 --> Compose
  Compose --> Net[lh-network]
```

## CLI vs dashboard responsibilities

```mermaid
flowchart TB
  subgraph Dash["Dashboard Python"]
    D1[Detect · materialize · validate]
    D2[AI onboarding stream]
    D3[Control API · Hosted apps UI]
  end
  subgraph Shared["Shared contract"]
    SCH[schema.py · load_effective_manifest]
  end
  subgraph CLI2["leco-devops CLI"]
    C1[ecosystem-register]
    C2[deploy · offload]
    C3[traefik merge · provision-local-cf]
  end
  D1 --> SCH
  D1 -->|subprocess| C1
  D3 -->|subprocess| C2
  C1 --> SCH
  C2 --> SCH
  C1 --> C3
```

## wsp: materialize path

```mermaid
flowchart LR
  W[wsp:MyRepo/apps/api] --> Det[Detect scan]
  Det --> Gen[Generate YAML]
  Gen --> AV[hosting/app-available/slug/]
  Gen --> Sym[source → workspace-parent/MyRepo/...]
  AV --> Reg[Register]
  Sym --> Reg
  Reg --> Live[myapp.lh via Traefik]
```

## Related topics

- [Onboarding overview](help:onboarding-overview)
- [Hosting layout](help:hosting-layout)
- [Registration flow developer](help:dev-registration-flow)
- [Developer's guide](help:dev-overview)
