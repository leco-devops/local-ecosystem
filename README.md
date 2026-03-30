# 🚀 Local Ecosystem — AI Stack Platform (Full Detailed Guide)

---

# 📌 Overview

This project is a **local, production-like infrastructure platform** built using:

* **Traefik** → Reverse proxy + routing
* **mkcert** → Trusted local HTTPS certificates
* **Ollama** → Local LLM runtime
* **Open WebUI** → AI interface
* **n8n** → Automation engine
* **PostgreSQL** → Database

It provides:

```text
*.lh / *.lm → Local domain routing
HTTP + HTTPS → Fully supported
Docker-based → Isolated & reproducible
```

---
# 🧠 What You’re Building (IMPORTANT)

👉 A **Local Domain Ecosystem (Mini Cloud Platform)**

Instead of:

```text
localhost:5678
localhost:3000
localhost:11434
```

You will use:

```text
n8n.lh        → localhost:5678
ai.lh         → localhost:8080
ollama.lh     → localhost:11434
traefik.lh    → localhost:8080 (dashboard)
```

---

## 🧠 Key Insight

```text
You are not running apps.
You are running a LOCAL CLOUD PLATFORM.
```

---

# 🏗️ Architecture (Simple & Clean)

```text
Browser
   ↓
*.lh / *.lm (Local DNS via dnsmasq)
   ↓
Traefik (Reverse Proxy :80 / :443)
   ↓
Docker Network (lh-network)
   ↓
Containers (n8n, webui, ollama, postgres)
```

---

# 📁 Directory Structure

Base Path:

```bash
~/Working/GitHub/local-ecosystem
```

```
local-ecosystem/
│
├── ai-stack/
│   ├── services/
│   │   ├── ollama.sh
│   │   ├── webui.sh
│   │   ├── n8n.sh
│   │   └── postgres.sh
│   │
│   ├── config/
│   │   └── dynamic.yml
│   │
│   ├── core.sh
│   ├── ai-stack.sh
│   └── README.md
│
├── traefik/
│   └── dynamic.yml
│
├── certs/
│   ├── wildcard.lh.pem
│   └── wildcard.lh-key.pem
│
├── bootstrap-ai-stack.sh
└── fix-and-run.sh
```

---

# 🌐 Step 1 — Local Domain Mapping (*.lh / *.lm)

---

## 🔹 Install dnsmasq

```bash
brew install dnsmasq
```

---

## 🔹 Configure dnsmasq

```bash
echo "address=/.lh/127.0.0.1" >> $(brew --prefix)/etc/dnsmasq.conf
```

Optional:

```bash
echo "address=/.lm/127.0.0.1" >> $(brew --prefix)/etc/dnsmasq.conf
```

---

## 🔹 Start dnsmasq

```bash
sudo brew services start dnsmasq
```

---

## 🔹 Create resolver

```bash
sudo mkdir -p /etc/resolver
```

---

## 🔹 Add resolver for `.lh`

```bash
echo "nameserver 127.0.0.1" | sudo tee /etc/resolver/lh
```

---

## 🔹 Add resolver for `.lm` (optional)

```bash
echo "nameserver 127.0.0.1" | sudo tee /etc/resolver/lm
```

---

## 🔹 Test

```bash
ping ai.lh
```

Expected:

```text
127.0.0.1
```

---

# 🐳 Step 2 — Docker Setup

---

## Install Docker Desktop

Use:

Docker Desktop

---

## Verify Docker

```bash
docker ps
```

---

## Create Network

```bash
docker network create lh-network
```

---

# 🔐 Step 3 — HTTPS Setup (mkcert)

---

## Install mkcert

```bash
brew install mkcert
```

---

## Install CA

```bash
mkcert -install
```

---

## Fix Java Issue (if occurs)

```bash
unset JAVA_HOME
```

---

## Generate Certificate

```bash
cd ~/Working/GitHub/local-ecosystem/certs
mkcert "*.lh"
```

---

## Verify

```bash
ls
```

Expected:

```text
wildcard.lh.pem
wildcard.lh-key.pem
```

---

# 🔑 Step 4 — Trust Certificate (CRITICAL)

---

## Open Keychain Access

Search:

```text
Keychain Access
```

---

## Import Root CA

```bash
mkcert -CAROOT
```

Import:

```text
rootCA.pem
```

---

## Set Trust

* Open certificate
* Expand Trust
* Set:

```text
Always Trust
```

---

## ❗ IMPORTANT

```text
System Keychain ✅
iCloud Keychain ❌
```

---

# 🔀 Step 5 — Traefik Setup

---

## Start Traefik

```bash
docker run -d \
  --name traefik \
  --network lh-network \
  -p 80:80 \
  -p 443:443 \
  -p 8080:8080 \
  -v ~/Working/GitHub/local-ecosystem/traefik:/etc/traefik \
  -v ~/Working/GitHub/local-ecosystem/certs:/certs \
  traefik:v3.0 \
  --api.insecure=true \
  --providers.file.filename=/etc/traefik/dynamic.yml \
  --entrypoints.web.address=:80 \
  --entrypoints.websecure.address=:443
```

---

# 🔀 Step 6 — Routing Explained

Each service has:

```text
HTTP router  → port 80
HTTPS router → port 443
```

Example:

```yaml
n8n-http:
n8n-https:
```

---

# ⚙️ Step 7 — Services

---

## Ollama

```bash
docker run -d \
  --name ollama \
  --network lh-network \
  -v ollama:/root/.ollama \
  ollama/ollama
```

**Pinned models (AI stack):** edit `ai-stack/config/ollama-pinned-models.txt` (one model name per line). When the `ollama` service starts via `ai-stack/services/ollama.sh`, it pulls those models in the background. To pull again into a running container:

```bash
./ai-stack/ai-stack.sh ollama-pull-models
```

The ops dashboard at **http://localhost.lh** → **Infrastructure** shows **Ollama models** (pinned vs installed, pull/delete/unload). Model actions use the same optional control token as the **Control** tab (`DASHBOARD_CONTROL_TOKEN`, or `X-Control-Token` / saved browser token). The dashboard also caches recent overview and metrics in **localStorage** so a reload can show the last good snapshot while APIs catch up.

---

## Open WebUI

```bash
docker run -d \
  --name open-webui \
  --network lh-network \
  -e OLLAMA_BASE_URL=http://ollama:11434 \
  -v open-webui:/app/backend/data \
  ghcr.io/open-webui/open-webui:main
```

---

## PostgreSQL

```bash
docker run -d \
  --name n8n_postgres \
  --network lh-network \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=n8n \
  -v n8n_postgres_data:/var/lib/postgresql/data \
  postgres:15
```

---

## n8n (CRITICAL CONFIG)

```bash
docker run -d \
  --name n8n \
  --network lh-network \
  -e DB_TYPE=postgresdb \
  -e DB_POSTGRESDB_HOST=n8n_postgres \
  -e DB_POSTGRESDB_DATABASE=n8n \
  -e DB_POSTGRESDB_USER=postgres \
  -e DB_POSTGRESDB_PASSWORD=password \
  -e N8N_HOST=n8n.lh \
  -e N8N_PROTOCOL=http \
  -e N8N_SECURE_COOKIE=false \
  -e N8N_TRUST_PROXY=true \
  -v n8n_data:/home/node/.n8n \
  docker.n8n.io/n8nio/n8n
```

---

# ⚠️ Step 8 — HTTP vs HTTPS (IMPORTANT)

---

## Problem

```text
secure cookie + insecure URL
```

---

## Root Cause

```text
Traefik sends HTTPS headers
n8n expects secure cookies
```

---

## Fix

```bash
N8N_SECURE_COOKIE=false
```

---

## Final Behavior

| URL            | Status |
| -------------- | ------ |
| http://n8n.lh  | ✅      |
| https://n8n.lh | ✅      |

---

# 🌍 Access URLs

```text
https://traefik.lh
https://ai.lh
https://n8n.lh
https://ollama.lh
http://localhost.lh
http://r2.lh
http://kv.lh
http://d1.lh
http://workers.lh
http://autoscale.lh
http://minio-console.lh
```

**Ops dashboard (`localhost.lh`):** overview, infrastructure (including Cloudflare Local + Ollama models), metrics history charts, logs, embedded docs, and **Control** for stack actions. After a control action completes, cards refresh automatically; no separate “refresh cards” step. Active tab and cached overview/metrics are restored from the browser when you reopen the page (within ~48 hours).

---

# 🧪 Step 9 — Test

Open all URLs in browser.

---

# 🧹 CLI Usage

```bash
./ai-stack.sh menu
./ai-stack.sh start
./ai-stack.sh stop
./ai-stack.sh restart
./ai-stack.sh pause [service]
./ai-stack.sh unpause [service]
./ai-stack.sh status [service]
./ai-stack.sh remove [service]
./ai-stack.sh logs n8n
./ai-stack.sh repair-network
./ai-stack.sh reset
./ai-stack.sh start cloudflare-local
./ai-stack.sh logs cloudflare-local
```

---

# 🔥 Challenges Faced

---

## Docker Socket (macOS)

* `/var/run/docker.sock` issue
* Fixed via file provider

---

## mkcert Java Error

```bash
unset JAVA_HOME
```

---

## Certificate Trust

* iCloud ❌
* System ✅

---

## n8n Cookie Issue

* Fixed via env config

---

## Traefik Routing

* Fixed via proper dynamic.yml

---

## Bad Gateway (network mismatch)

* Fixed by ensuring `traefik`, `open-webui`, `ollama`, `n8n_postgres`, and `n8n` are attached to `lh-network`
* Use:

```bash
./ai-stack.sh repair-network
```

---

# 🎯 Final Outcome

```text
✔ Domain-based routing
✔ Trusted HTTPS
✔ Reverse proxy
✔ Modular services
✔ Production-like local setup
```

---

# 💬 Final Thought

```text
This is not a dev setup.
This is a LOCAL CLOUD PLATFORM.
```

---
