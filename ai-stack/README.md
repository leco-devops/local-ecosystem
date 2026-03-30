# AI stack orchestration

Shell entry points for Docker services on **`lh-network`**.

| Entry | Purpose |
|--------|---------|
| **`ai-stack.sh`** | Interactive menu or `./ai-stack.sh <action> [service]` from repo root |
| **`core.sh`** | Sourced by `ai-stack.sh` — start order, `repair-network`, `bulk_ecosystem` |
| **`services/*.sh`** | Per-container `start` / `stop` / `deploy` / … |

**Documentation:** [../docs/SETUP.md](../docs/SETUP.md) (full setup) · [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) (operations) · [../README.md](../README.md) (overview).

**macOS host metrics:** [scripts/macos-write-cpu-temp.sh](scripts/macos-write-cpu-temp.sh) · [scripts/macos-host-metrics-scheduler.sh](scripts/macos-host-metrics-scheduler.sh) (installed when `services/dashboard.sh` starts the dashboard).
