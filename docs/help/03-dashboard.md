# Dashboard tour

Open **`https://localhost.lh`** (or `http://localhost.lh`).

## Primary tabs

| Tab | Purpose |
|-----|---------|
| **Overview** | Live charts, URL probe summary, hosted app links |
| **Reference** | All `*.lh` URLs grouped by category |
| **Infrastructure** | Health, service cards, **Ollama/AirLLM model managers**, CF local, Docker inventory |
| **Metrics** | Host CPU/RAM/temp history |
| **Control** | Start/stop/restart/pause ecosystem services |
| **Hosted apps** | Registered apps from `leco-registry.yaml` |
| **Routes** | Traefik `hosting/traefik/dynamic.yml` editor |
| **Logs** | Per-container log tail |
| **Help** | This manual (`/help`) |

**Docs** and **Develop** tabs exist in the page footer and deep links (`/?tab=docsTab`) but are not in the top tab bar — use footer links or **Help → Further reading**.

## Auto-refresh

Header **Auto refresh** (5s–60s) reloads Overview/Infrastructure data. Use **Refresh now** for an immediate pull.

## Control token

Some actions (model install/remove, Control bulk ops) require `DASHBOARD_CONTROL_TOKEN` when set in `ecosystem-stack/services/dashboard.sh`. Enter the token on the **Control** tab once; it is stored in browser `localStorage`.

## Where is the Ollama / AirLLM model UI?

1. Click **Infrastructure** in the top nav.
2. Use the **jump bar** (sticky) → **Ollama** or **AirLLM**, or scroll to sections **5 · Ollama** and **6 · AirLLM**.
3. Look for the purple **Model manager** card — not only the service cards in section 2.

Service cards on Overview/Infrastructure section 2 show model *tables* when data is loaded; the **Install / Load / Popular** controls are in section 5/6.

Next: [Infrastructure tab](help:dash-infra) · [Control](help:dash-control)
