# Browser rendering (local Docker)

This repo ships **`browser-rendering-local`** — a FastAPI service plus headless Chromium, configurable as:

- **`BROWSER_BACKEND=playwright`** — Playwright-managed Chromium  
- **`BROWSER_BACKEND=chromium`** — system Chromium over **Chrome DevTools Protocol**

It is **not** Cloudflare’s proprietary [Browser Rendering](https://developers.cloudflare.com/browser-rendering/) runtime; it is **local dev emulation** with a similar *shape* (screenshot, PDF, HTML).

## URLs

- Traefik: **http://browser.lh** / **https://browser.lh**  
- Inside Docker: **http://browser-rendering-local:8085**

## Operations

- **Control** tab: target **Browser rendering (local)** (compose service) or restart the whole **Cloudflare local** stack.  
- Compose env: see `cloudflare-local/docker-compose.yml` → `browser-rendering-local`.

## Optional production bridge

With **`CLOUDFLARE_ACCOUNT_ID`** and **`CLOUDFLARE_API_TOKEN`** set on the container, **`POST /cf/screenshot`** proxies to Cloudflare’s REST API (path in `app.py` — verify against current docs if you get 404).

See also [BROWSER_RENDERING_PRODUCTION.md](./BROWSER_RENDERING_PRODUCTION.md).
