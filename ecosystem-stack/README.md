# Ecosystem stack orchestration

Shell entry points for Docker services on **`lh-network`**.

| Entry | Purpose |
|--------|---------|
| **`ecosystem-stack.sh`** | Interactive menu or `./ecosystem-stack.sh <action> [service]` from repo root |
| **`install-foundation.sh`** | Guided foundation check/install + per-service start selection |
| **`core.sh`** | Sourced by `ecosystem-stack.sh` — start order, `repair-network`, `bulk_ecosystem` |
| **`services/*.sh`** | Per-container `start` / `stop` / `deploy` / … |

**Documentation:** [../docs/SETUP.md](../docs/SETUP.md) (full setup) · [../docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) (operations) · [../docs/PROJECT.md](../docs/PROJECT.md) (repository guide) · [../README.md](../README.md) (landing).

**macOS host metrics:** [scripts/macos-write-cpu-temp.sh](scripts/macos-write-cpu-temp.sh) · [scripts/macos-host-metrics-scheduler.sh](scripts/macos-host-metrics-scheduler.sh) (installed when `services/dashboard.sh` starts the dashboard).
