# Cloudflare Browser Rendering (production)

[Browser Rendering](https://developers.cloudflare.com/browser-rendering/) runs **only on Cloudflare’s network**. The local Docker adapter in this repository **does not** embed that product.

## When to use what

| Environment | Approach |
|-------------|----------|
| **Local dev** | `browser-rendering-local` adapter (`BROWSER_BACKEND=playwright` or `chromium`) |
| **Production / parity tests** | Cloudflare [REST API](https://developers.cloudflare.com/browser-rendering/rest-api/) or [Workers bindings](https://developers.cloudflare.com/browser-rendering/workers-bindings/) with `wrangler deploy` |

## Workers + bindings (outline)

1. Create a Worker project with `wrangler.toml` enabling the **browser rendering** binding (see Cloudflare docs for the current `wrangler` schema).  
2. Use **`@cloudflare/puppeteer`** or the official Playwright fork for Workers as documented.  
3. Deploy with **`wrangler deploy`** — **not** via the local Miniflare container in this repo (Miniflare here is a simple fetch handler only).

## Links

- [Get started](https://developers.cloudflare.com/browser-rendering/get-started/)  
- [REST API](https://developers.cloudflare.com/browser-rendering/rest-api/)  
- [Puppeteer on Workers](https://developers.cloudflare.com/browser-rendering/puppeteer/)
