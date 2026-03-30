"""
Local Browser Rendering–style HTTP API (Playwright or system Chromium + CDP).
Not Cloudflare production — see README.md.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, HttpUrl

from engines import get_browser_engine
from engines.base import BrowserEngine

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_engine: BrowserEngine | None = None
_sem = None


def _max_concurrent() -> int:
    try:
        return max(1, int(os.environ.get("BROWSER_MAX_CONCURRENT", "2")))
    except ValueError:
        return 2


def _timeout_ms() -> int:
    try:
        return max(5000, int(os.environ.get("BROWSER_TIMEOUT_MS", "45000")))
    except ValueError:
        return 45000


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine, _sem
    import asyncio

    _engine = get_browser_engine()
    _sem = asyncio.Semaphore(_max_concurrent())
    log.info("browser-rendering-local backend=%s", _engine.name)
    yield
    if _engine:
        await _engine.close()
        _engine = None


app = FastAPI(title="Browser Rendering (local)", lifespan=lifespan)


class UrlPayload(BaseModel):
    url: HttpUrl
    full_page: bool = False


PANEL_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Browser rendering — local</title>
<style>
:root{--bg:#0b1220;--card:#151d2e;--bd:#2d3a52;--txt:#e8edf7;--a:#fb923c}
body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:1.25rem;line-height:1.55;max-width:48rem}
a{color:var(--a)}code{background:#1e293b;padding:.15rem .35rem;border-radius:4px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:1rem;margin:1rem 0}
.muted{opacity:.85;font-size:.9rem}
</style></head><body>
<h1>Browser rendering (local emulation)</h1>
<p class="muted">Backend: <strong>__BACKEND__</strong>. Not Cloudflare edge — dev-only headless browser.</p>
<div class="card"><strong>API</strong>
<ul>
<li><a href="/health">GET /health</a></li>
<li>POST /screenshot <code>{"url":"https://example.com","full_page":false}</code></li>
<li>POST /pdf <code>{"url":"https://example.com"}</code></li>
<li>POST /html <code>{"url":"https://example.com"}</code> — outer HTML</li>
</ul></div>
<div class="card"><strong>Switch backend</strong>
<p>Set env <code>BROWSER_BACKEND=playwright</code> (default) or <code>BROWSER_BACKEND=chromium</code> (system Chromium + CDP).
Optional <code>CHROMIUM_PATH</code>, <code>BROWSER_TIMEOUT_MS</code>, <code>BROWSER_MAX_CONCURRENT</code>.</p></div>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/panel", response_class=HTMLResponse)
async def panel():
    b = _engine.name if _engine else os.environ.get("BROWSER_BACKEND", "playwright")
    return HTMLResponse(PANEL_HTML.replace("__BACKEND__", b))


@app.get("/health")
async def health():
    b = _engine.name if _engine else (os.environ.get("BROWSER_BACKEND") or "playwright")
    out = {
        "ok": True,
        "service": "browser-rendering-local",
        "backend": b,
        "max_concurrent": _max_concurrent(),
        "timeout_ms_default": _timeout_ms(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    aid = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    tok = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    out["cloudflare_bridge_configured"] = bool(aid and tok)
    return JSONResponse(out)


async def _run(engine_fn):
    if _engine is None or _sem is None:
        raise HTTPException(503, "engine not ready")
    async with _sem:
        try:
            return await engine_fn(_engine)
        except Exception as exc:
            log.exception("browser op failed")
            raise HTTPException(502, str(exc)) from exc


@app.post("/screenshot")
async def screenshot(body: UrlPayload):
    t = _timeout_ms()

    async def go(eng: BrowserEngine):
        return await eng.screenshot(str(body.url), timeout_ms=t, full_page=body.full_page)

    data = await _run(go)
    return Response(content=data, media_type="image/png")


@app.post("/pdf")
async def pdf(body: UrlPayload):
    t = _timeout_ms()

    async def go(eng: BrowserEngine):
        return await eng.pdf(str(body.url), timeout_ms=t)

    data = await _run(go)
    return Response(content=data, media_type="application/pdf")


@app.post("/html")
async def html(body: UrlPayload):
    t = _timeout_ms()

    async def go(eng: BrowserEngine):
        return await eng.html(str(body.url), timeout_ms=t)

    text = await _run(go)
    return JSONResponse({"ok": True, "url": str(body.url), "html": text})


# --- Optional: forward to Cloudflare Browser Rendering REST (production parity) ---


class CfUrlPayload(BaseModel):
    url: HttpUrl
    account_id: str | None = Field(None, description="Override CLOUDFLARE_ACCOUNT_ID")


@app.post("/cf/screenshot")
async def cf_screenshot_proxy(body: CfUrlPayload):
    """If CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are set, call Cloudflare REST API."""
    account = (body.account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")).strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token:
        raise HTTPException(
            400,
            "Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN for /cf/* routes",
        )
    # REST shape per Cloudflare docs (may evolve — check dashboard if this 404s)
    api = f"https://api.cloudflare.com/client/v4/accounts/{account}/browser-rendering/screenshot"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"url": str(body.url)}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(api, json=payload, headers=headers)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:2000])
    ct = r.headers.get("content-type", "image/jpeg")
    return Response(content=r.content, media_type=ct)
