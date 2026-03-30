addEventListener("fetch", (event) => {
  event.respondWith(handleRequest(event.request));
});

const PANEL_HTML = `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Workers runtime — local</title>
<style>
body{font-family:system-ui,sans-serif;background:#0b1220;color:#e8edf7;margin:0;padding:1.25rem;line-height:1.55;max-width:48rem}
a{color:#fb923c}code{background:#1e293b;padding:.15rem .35rem;border-radius:4px}
.card{background:#151d2e;border:1px solid #2d3a52;border-radius:10px;padding:1rem;margin:1rem 0}
</style></head><body>
<h1>Workers (Miniflare)</h1>
<p>Local fetch handler — not Cloudflare production.</p>
<div class="card"><strong>Endpoints</strong>
<ul>
<li><a href="/">GET /</a> — JSON hello</li>
<li><a href="/health">GET /health</a> — JSON health</li>
</ul></div>
<div class="card"><strong>Troubleshooting 502 via Traefik</strong>
<p>Ensure <code>workers-runtime</code> is running and on <code>lh-network</code>:</p>
<pre style="background:#0f172a;padding:.75rem;border-radius:6px;font-size:.85rem">docker compose -f cloudflare-local/docker-compose.yml up -d workers-runtime
docker network inspect lh-network | grep workers-runtime</pre>
</div>
</body></html>`;

async function handleRequest(request) {
  const url = new URL(request.url);
  if (url.pathname === "/panel" || url.pathname === "/explorer") {
    return new Response(PANEL_HTML, {
      headers: { "content-type": "text/html;charset=utf-8" },
    });
  }
  if (url.pathname === "/health" || url.pathname === "/") {
    return new Response(
      JSON.stringify({
        ok: true,
        service: "workers-runtime",
        path: url.pathname,
        note: "Local Miniflare (Workers fetch handler)",
      }),
      { headers: { "content-type": "application/json" } },
    );
  }
  return new Response(JSON.stringify({ error: "not_found", path: url.pathname }), {
    status: 404,
    headers: { "content-type": "application/json" },
  });
}
