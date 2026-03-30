"""Minimal Telegram Bot API webhook receiver for local dev."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="Telegram gateway (local)")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
LAST_UPDATE: dict | None = None


@app.get("/health")
def health():
    return JSONResponse(
        {
            "ok": True,
            "service": "telegram-gateway",
            "bot_configured": bool(BOT_TOKEN),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get("/panel", response_class=HTMLResponse)
def panel():
    return HTMLResponse(
        """<!DOCTYPE html><html><head><meta charset="utf-8"/><title>Telegram gateway</title></head>
<body style="font-family:system-ui;max-width:42rem;margin:1.5rem">
<h1>Telegram gateway (local)</h1>
<p>Set <code>TELEGRAM_BOT_TOKEN</code> in compose. Point your bot webhook to
<code>https://telegram.lh/webhook</code> (or tunnel) — Telegram requires HTTPS for webhooks.</p>
<ul>
<li><a href="/health">GET /health</a></li>
<li>POST /webhook — Bot API updates JSON</li>
<li>POST /send — JSON <code>{"chat_id":"…","text":"hello"}</code> (uses Bot API sendMessage)</li>
</ul>
</body></html>"""
    )


@app.post("/webhook")
async def webhook(request: Request):
    global LAST_UPDATE
    try:
        body = await request.json()
    except Exception:
        body = {"raw": (await request.body()).decode("utf-8", errors="replace")}
    LAST_UPDATE = {"received_at": datetime.now(timezone.utc).isoformat(), "update": body}
    return {"ok": True}


@app.get("/last")
def last():
    if not LAST_UPDATE:
        return JSONResponse({"ok": True, "last": None})
    return JSONResponse({"ok": True, "last": LAST_UPDATE})


@app.post("/send")
async def send(payload: dict):
    if not BOT_TOKEN:
        return JSONResponse({"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}, status_code=400)
    chat_id = payload.get("chat_id")
    text = payload.get("text", "")
    if chat_id is None:
        return JSONResponse({"ok": False, "error": "chat_id required"}, status_code=400)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json={"chat_id": chat_id, "text": text})
    return JSONResponse({"ok": r.is_success, "status": r.status_code, "body": r.text[:2000]})
