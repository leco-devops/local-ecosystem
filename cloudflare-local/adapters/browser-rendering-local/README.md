# Browser rendering (local)

Dev-local **headless browser** HTTP API, similar in *purpose* to [Cloudflare Browser Rendering](https://developers.cloudflare.com/browser-rendering/), but running **in Docker** with either:

- **`BROWSER_BACKEND=playwright`** (default) — Playwright’s bundled Chromium  
- **`BROWSER_BACKEND=chromium`** — system `/usr/bin/chromium` over **Chrome DevTools Protocol** (no Playwright on that code path)

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | JSON status + active `backend` |
| GET | `/panel` | Short HTML summary |
| POST | `/screenshot` | JSON `{"url":"https://…","full_page":false}` → PNG |
| POST | `/pdf` | JSON `{"url":"https://…"}` → PDF |
| POST | `/html` | JSON `{"url":"https://…"}` → JSON with `html` string |

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `BROWSER_BACKEND` | `playwright` | `playwright` or `chromium` |
| `CHROMIUM_PATH` | `/usr/bin/chromium` | Executable for `chromium` backend |
| `BROWSER_TIMEOUT_MS` | `45000` | Navigation timeout |
| `BROWSER_MAX_CONCURRENT` | `2` | Semaphore for parallel jobs |

## Optional Cloudflare REST bridge

If `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are set (Browser Rendering permissions), **`POST /cf/screenshot`** proxies to Cloudflare’s REST API for production parity checks. See [REST API](https://developers.cloudflare.com/browser-rendering/rest-api/). If Cloudflare changes paths, adjust `app.py`.

## Production Cloudflare

Real Browser Rendering runs on Cloudflare’s network only. For Workers **bindings**, use Wrangler and deploy a Worker (not Miniflare). See `cloudflare-local/docs/BROWSER_RENDERING_PRODUCTION.md` in this repo.
