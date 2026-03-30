from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any

import httpx
import websockets

from .base import BrowserEngine

log = logging.getLogger(__name__)


async def _cdp_send(ws, method: str, params: dict | None = None, req_id: int | None = None) -> dict[str, Any]:
    rid = req_id if req_id is not None else id(asyncio.current_task()) % 1_000_000
    msg = {"id": rid, "method": method, "params": params or {}}
    await ws.send(json.dumps(msg))
    while True:
        raw = await ws.recv()
        data = json.loads(raw)
        if "id" not in data:
            continue
        if data.get("id") == rid:
            if "error" in data:
                raise RuntimeError(data["error"])
            return data.get("result", {})


class ChromiumCdpEngine(BrowserEngine):
    """System Chromium controlled via Chrome DevTools Protocol (no Playwright on this path)."""

    name = "chromium"

    def __init__(self, chromium_path: str | None = None) -> None:
        self._path = chromium_path or os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium")
        self._proc: asyncio.subprocess.Process | None = None
        self._port: int | None = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> int:
        async with self._lock:
            if self._proc and self._proc.returncode is None and self._port:
                return self._port
            if self._proc:
                try:
                    self._proc.terminate()
                    await asyncio.wait_for(self._proc.wait(), timeout=5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None

            self._port = _pick_free_port()
            args = [
                self._path,
                f"--remote-debugging-port={self._port}",
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--window-size=1280,720",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ]
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(0.6)
            if self._proc.returncode is not None:
                raise RuntimeError("chromium exited immediately; check CHROMIUM_PATH and deps")
            return self._port

    async def _with_tab(self, url: str, timeout_ms: int, work):
        port = await self._ensure_browser()
        tab_url = f"http://127.0.0.1:{port}/json/new"
        async with httpx.AsyncClient(timeout=timeout_ms / 1000 + 15) as client:
            r = await client.get(tab_url)
            r.raise_for_status()
            tab = r.json()
            ws_url = tab.get("webSocketDebuggerUrl")
            if not ws_url:
                raise RuntimeError("no webSocketDebuggerUrl from /json/new")
            async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
                await _cdp_send(ws, "Page.enable", {})
                await _cdp_send(ws, "Runtime.enable", {})
                await _cdp_send(ws, "Page.navigate", {"url": url})
                sec = max(5.0, timeout_ms / 1000 + 5)
                deadline = asyncio.get_event_loop().time() + sec
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        data = json.loads(raw)
                        if data.get("method") == "Page.loadEventFired":
                            break
                    except asyncio.TimeoutError:
                        continue
                return await work(ws)

    async def screenshot(self, url: str, *, timeout_ms: int, full_page: bool = False) -> bytes:
        async def work(ws):
            res = await _cdp_send(
                ws,
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": full_page},
            )
            b64 = res.get("data")
            if not b64:
                raise RuntimeError("captureScreenshot returned no data")
            return base64.b64decode(b64)

        return await self._with_tab(url, timeout_ms, work)

    async def pdf(self, url: str, *, timeout_ms: int) -> bytes:
        async def work(ws):
            res = await _cdp_send(
                ws,
                "Page.printToPDF",
                {"printBackground": True, "preferCSSPageSize": True},
            )
            b64 = res.get("data")
            if not b64:
                raise RuntimeError("printToPDF returned no data")
            return base64.b64decode(b64)

        return await self._with_tab(url, timeout_ms, work)

    async def html(self, url: str, *, timeout_ms: int) -> str:
        async def work(ws):
            res = await _cdp_send(
                ws,
                "Runtime.evaluate",
                {"expression": "document.documentElement.outerHTML", "returnByValue": True},
            )
            out = res.get("result", {}).get("value")
            if out is None:
                raise RuntimeError("could not read document HTML")
            return str(out)

        return await self._with_tab(url, timeout_ms, work)

    async def close(self) -> None:
        async with self._lock:
            if self._proc:
                try:
                    self._proc.terminate()
                    await asyncio.wait_for(self._proc.wait(), timeout=5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None
            self._port = None


def _pick_free_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    _, port = s.getsockname()
    s.close()
    return int(port)
