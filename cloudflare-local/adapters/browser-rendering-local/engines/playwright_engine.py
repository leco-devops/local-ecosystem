from __future__ import annotations

import asyncio

from playwright.async_api import async_playwright

from .base import BrowserEngine


class PlaywrightEngine(BrowserEngine):
    name = "playwright"

    def __init__(self) -> None:
        self._pw_cm = None
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self):
        if self._browser:
            return
        self._pw_cm = async_playwright()
        self._playwright = await self._pw_cm.__aenter__()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )

    async def screenshot(self, url: str, *, timeout_ms: int, full_page: bool = False) -> bytes:
        async with self._lock:
            await self._ensure_browser()
            page = await self._browser.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                return await page.screenshot(type="png", full_page=full_page)
            finally:
                await page.close()

    async def pdf(self, url: str, *, timeout_ms: int) -> bytes:
        async with self._lock:
            await self._ensure_browser()
            page = await self._browser.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                return await page.pdf(print_background=True)
            finally:
                await page.close()

    async def html(self, url: str, *, timeout_ms: int) -> str:
        async with self._lock:
            await self._ensure_browser()
            page = await self._browser.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                return await page.content()
            finally:
                await page.close()

    async def close(self) -> None:
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._pw_cm and self._playwright is not None:
                await self._pw_cm.__aexit__(None, None, None)
                self._pw_cm = None
                self._playwright = None
