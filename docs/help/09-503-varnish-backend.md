# 503 Varnish “Backend fetch failed”

## Symptom

Browser shows a **Varnish** error page (not Traefik **502**):

- **Error 503 Backend fetch failed**
- Footer: **Varnish cache server**

Typical on **`https://<slug>.lh/...`** right after **Recreate**, **restart**, or first deploy of a **Node + Varnish** hosted app.

## 502 vs 503

| Code | Layer | Meaning |
|------|--------|---------|
| **502** | Traefik | Cannot reach the upstream container (wrong host/port, not on `lh-network`) |
| **503** | Varnish | Upstream is Varnish, but Varnish cannot fetch from its **backend** (usually Express on `:3000`) |

## Common causes

### 1. Server still starting (most common after restart)

The `server` container may run **apt-get**, **npm install**, and Chromium setup for **1–3 minutes** before Express listens.

**Fix (template / AI-generated compose):**

- `server` **healthcheck** → `http://127.0.0.1:3000<health-path>`
- `varnish` **`depends_on: server: condition: service_healthy`**

Wait until `docker ps` shows **`(healthy)`** on `<slug>-server` before testing in the browser.

### 2. Server crash loop (`sudo varnishncsa`)

Some apps start a Worker that runs **`sudo varnishncsa`** on the host. Inside Docker there is **no sudo** and **no varnishncsa** in the Node container (Varnish is a **separate** service).

**Fix:**

- Hosting overlay: **`LECO_DISABLE_VARNISH_NCSA: "true"`** and **`LECO_VARNISH_HOST: varnish`**
- Upstream app: skip host log monitor when `LECO_VARNISH_HOST` is set (see botfeed `varnish.js`)

### 3. VCL points at `127.0.0.1:3000`

Inside the Varnish container, `127.0.0.1` is Varnish itself, not the API.

**Fix:** `conf/varnish/default.vcl` backend `.host = "<slug>-server"`, `.port = "3000"`. See `hosting/samples/sample-node-varnish-multiprocess/conf/varnish/default.vcl`.

### 4. Wrong health path

Varnish healthcheck and VCL probe must use the **same path** the app exposes (e.g. `/alb-health-check`, not `/health`).

Use scaffold **`--health-path /alb-health-check`** when applicable.

## Checklist

```bash
# 1. Server healthy?
docker ps --filter name=<slug>-server

# 2. Backend reachable from Varnish?
docker exec <slug>-varnish wget -qO- http://<slug>-server:3000/alb-health-check

# 3. Through Traefik
curl -sk -o /dev/null -w '%{http_code}\n' https://<slug>.lh/alb-health-check

# 4. Server logs (crash loop?)
docker logs <slug>-server --tail 50
```

## After 503 is gone: 403 on render URLs

If **`https://<slug>.lh/<client>/http://example.com`** returns **403 Request is not authorized**, the proxy stack is fine — MongoDB has no **`clients`** document for that **`client_conf_key`**. Import or seed client data.

## References

- Template: `hosting/samples/sample-node-varnish-multiprocess/`
- Traefik runbook: [HOSTED_APPS_TRAEFIK_RUNBOOK.md](../HOSTED_APPS_TRAEFIK_RUNBOOK.md)
- Scaffold: `leco-devops scaffold … --template sample-node-varnish-multiprocess --health-path /alb-health-check`
