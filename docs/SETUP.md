# Complete setup guide — Local Ecosystem

This document walks through **first-time installation** from an empty machine to a working local platform: DNS, TLS, Traefik, AI stack services, optional Cloudflare-local emulation, and macOS host metrics for the ops dashboard.

Use **[DEPLOYMENT.md](DEPLOYMENT.md)** for day-two operations (updates, stopping services, troubleshooting).

---

## 1. What you are installing

| Layer | Role |
|--------|------|
| **dnsmasq + resolver** | Resolve `*.lh` (and optionally `*.lm`) to `127.0.0.1` |
| **Docker** | All application services run as containers on **`lh-network`** |
| **mkcert** | Trusted TLS certificates for `*.lh` under `certs/` |
| **Traefik** | Reverse proxy on **80 / 443 / 8080** (dashboard API on 8080) |
| **AI stack** | Ollama, Open WebUI, PostgreSQL, n8n, ops **dashboard**, optional **cloudflare-local** compose stack |

**Ops dashboard:** routed at **http://localhost.lh** (Traefik) or directly **http://localhost:8090** (`DASHBOARD_HOST_PORT` overrides the host port).

---

## 2. Prerequisites

- **macOS** or **Linux** (Windows: use WSL2 + Docker; adapt paths).
- **Docker Desktop** (or Docker Engine + Compose v2).
- **Homebrew** on macOS for dnsmasq / mkcert (Linux: use distro packages).
- **Git** to clone the repository.

Set a shell variable for the rest of this guide (adjust the path):

```bash
export REPO="$HOME/Working/GitHub/local-ecosystem"
cd "$REPO"
```

---

## 3. Clone the repository

```bash
git clone <your-remote-url> "$REPO"
cd "$REPO"
```

---

## 4. Local DNS (`*.lh`)

### 4.1 Install and configure dnsmasq (macOS / Homebrew)

```bash
brew install dnsmasq
echo "address=/.lh/127.0.0.1" >> "$(brew --prefix)/etc/dnsmasq.conf"
# Optional second TLD:
# echo "address=/.lm/127.0.0.1" >> "$(brew --prefix)/etc/dnsmasq.conf"
sudo brew services start dnsmasq
```

### 4.2 Resolver files (macOS)

```bash
sudo mkdir -p /etc/resolver
echo "nameserver 127.0.0.1" | sudo tee /etc/resolver/lh
# Optional:
# echo "nameserver 127.0.0.1" | sudo tee /etc/resolver/lm
```

### 4.3 Verify

```bash
ping -c 1 ai.lh
# Should resolve to 127.0.0.1
```

---

## 5. Docker

Install Docker and confirm:

```bash
docker ps
```

The stack scripts create **`lh-network`** automatically. You can still create it manually:

```bash
docker network create lh-network
```

---

## 6. TLS certificates (mkcert)

```bash
brew install mkcert
mkcert -install
```

If you hit a Java-related error during install:

```bash
unset JAVA_HOME
mkcert -install
```

Generate a wildcard cert for `*.lh` (paths match Traefik service mounts):

```bash
mkdir -p "$REPO/certs"
cd "$REPO/certs"
mkcert "*.lh"
```

You should have **`wildcard.lh.pem`** and **`wildcard.lh-key.pem`**. Traefik mounts **`$REPO/certs`** and **`$REPO/traefik`** (see `ai-stack/services/traefik.sh`).

**Trust:** `mkcert -install` normally installs the local CA. If the browser still warns, import the CA from `$(mkcert -CAROOT)/rootCA.pem` into the **System** keychain (not iCloud) and set **Always Trust** (macOS).

---

## 7. Start the core stack (recommended path)

From the repo root, use the service manager (paths are relative to **`$REPO/ai-stack`**):

```bash
cd "$REPO"
./ai-stack/ai-stack.sh menu
```

Or non-interactive full start (respects dependency order in `ai-stack/core.sh`):

```bash
./ai-stack/ai-stack.sh start
```

**Default start order:** `traefik` → `postgres` → `ollama` → `webui` → `n8n` → `dashboard` → `cloudflare-local`.

| Script (`ai-stack/services/`) | Container / stack | Notes |
|------------------------------|-------------------|--------|
| `traefik.sh` | `traefik` | Reads `traefik/dynamic.yml` |
| `postgres.sh` | `n8n_postgres` | n8n database |
| `ollama.sh` | `ollama` | Pulls models from `ai-stack/config/ollama-pinned-models.txt` on start |
| `webui.sh` | `open-webui` | Points at Ollama |
| `n8n.sh` | `n8n` | Postgres-backed |
| `dashboard.sh` | `service-dashboard` | Builds image `local/service-dashboard:latest` |
| `cloudflare-local.sh` | Compose project | `cloudflare-local/docker-compose.yml` |

After any start or restart of multiple services, the tooling runs **network repair** so known containers attach to **`lh-network`** (see `NETWORK_CONTAINERS` in `ai-stack/core.sh`).

---

## 8. macOS: host CPU temperature for the dashboard

The dashboard runs in a **Linux** container; it cannot read Apple SMC directly. On **macOS**, `ai-stack/services/dashboard.sh`:

- Mounts **`~/.local-eco-host-metrics`** → `/host-mac-metrics` in the container.
- Sets **`DASHBOARD_HOST_CPU_TEMP_FILE`** to the CPU temp file inside that mount.

### 8.1 Automatic scheduling (LaunchAgent)

When you **start or deploy** the dashboard via **`dashboard.sh`**, the repo installs a **LaunchAgent** (`com.local-ecosystem.host-cpu-temp`) that runs **`ai-stack/scripts/macos-write-cpu-temp.sh`** every **30 seconds**. When you **stop** or **remove** the dashboard through the same script, the agent is **uninstalled**.

Manual control:

```bash
bash "$REPO/ai-stack/scripts/macos-host-metrics-scheduler.sh" status
bash "$REPO/ai-stack/scripts/macos-host-metrics-scheduler.sh" install   # if needed
bash "$REPO/ai-stack/scripts/macos-host-metrics-scheduler.sh" uninstall
```

Logs: **`~/Library/Logs/local-ecosystem-host-cpu-temp.out.log`** and **`.err.log`**.

The writer also updates **`~/.local-eco-host-metrics/writer_status.json`** (last run, source, errors). **`scheduler_meta.json`** in the same folder is written when the LaunchAgent is installed — the ops dashboard **Metrics** tab reads both for status and insights.

### 8.2 Optional: `osx-cpu-temp`

```bash
brew install osx-cpu-temp
```

On some Apple Silicon systems this reports **0°C**; the writer then falls back to **`sudo powermetrics`** and maps **thermal pressure** to a **proxy °C**, with an extra term from **`vm.loadavg` ÷ CPU count** so the value tracks heavy workloads (still not real die temperature; see `macos-write-cpu-temp.sh`).

### 8.3 Unattended `powermetrics`

For the LaunchAgent to run without prompting, configure **passwordless sudo** for:

```text
/usr/bin/powermetrics
```

Example sudoers line (replace `youruser`; use `sudo visudo`):

```text
youruser ALL=(root) NOPASSWD: /usr/bin/powermetrics
```

### 8.4 Manual `docker stop`

If you stop **`service-dashboard`** with plain **`docker stop`** (not **`dashboard.sh stop`**), the LaunchAgent **remains loaded**. Remove it with **`macos-host-metrics-scheduler.sh uninstall`** if you want host writes to stop.

---

## 9. Linux: richer “System” metrics in the dashboard

On **Linux**, `dashboard.sh` can mount host **`/proc`** and **`/sys`** so Metrics charts reflect the **host** (not only the container VM). This is automatic when the paths exist and are readable. See comments in **`ai-stack/services/dashboard.sh`**.

---

## 10. Ollama models

- Edit **`ai-stack/config/ollama-pinned-models.txt`** (one model per line).
- Restart Ollama or run:

```bash
./ai-stack/ai-stack.sh ollama-pull-models
```

The dashboard **Infrastructure** tab lists pinned vs installed models and can pull/delete/unload (optional **`DASHBOARD_CONTROL_TOKEN`**).

---

## 11. Cloudflare-local stack (optional)

Emulated R2/KV/D1/Workers-style APIs and related services:

```bash
cd "$REPO"
./cloudflare-local/scripts/bootstrap.sh
```

Or via AI stack:

```bash
./ai-stack/ai-stack.sh start cloudflare-local
```

Seed and smoke tests:

```bash
./cloudflare-local/scripts/seed.sh
./cloudflare-local/scripts/smoke.sh
```

Details: **[cloudflare-local/README.md](../cloudflare-local/README.md)** and **[cloudflare-local/docs/USER_MANUAL.md](../cloudflare-local/docs/USER_MANUAL.md)**.

---

## 12. Verification checklist

| Check | Command or URL |
|--------|----------------|
| DNS | `ping -c 1 n8n.lh` |
| Traefik API | http://localhost:8080 (insecure API; dev only) |
| Dashboard | http://localhost.lh or http://localhost:8090 |
| HTTPS routes | https://n8n.lh , https://ai.lh , etc. |
| Network | `./ai-stack/ai-stack.sh repair-network` then `docker network inspect lh-network` |
| Host temp file (macOS) | `cat ~/.local-eco-host-metrics/cpu_temp_c.txt` |
| Container sees temp | `docker exec service-dashboard cat /host-mac-metrics/cpu_temp_c.txt` |

---

## 13. n8n and HTTPS behind Traefik

n8n is configured for **proxy trust** and **`N8N_SECURE_COOKIE=false`** in the service script so both **http** and **https** front-door URLs work. If you change hostnames or TLS termination, review **`ai-stack/services/n8n.sh`** and Traefik labels/routes.

---

## 14. Related documentation

| Document | Purpose |
|----------|---------|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Updates, stop/start patterns, bulk operations, troubleshooting |
| [DEVELOPMENT_PLAYBOOK.md](DEVELOPMENT_PLAYBOOK.md) | Extending services, APIs, dashboard development |
| [../cloudflare-local/docs/ARCHITECTURE.md](../cloudflare-local/docs/ARCHITECTURE.md) | CF-local design |
| [../cloudflare-local/docs/USER_MANUAL.md](../cloudflare-local/docs/USER_MANUAL.md) | CF-local usage |
| [../README.md](../README.md) | Project hub and quick links |

---

## 15. Files removed from older README drafts

The repository may not include **`bootstrap-ai-stack.sh`** or **`fix-and-run.sh`**. Use **`./ai-stack/ai-stack.sh`** and per-service scripts under **`ai-stack/services/`** instead.
