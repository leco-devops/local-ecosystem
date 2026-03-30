from __future__ import annotations

import os

from .base import BrowserEngine
from .chromium_cdp import ChromiumCdpEngine
from .playwright_engine import PlaywrightEngine

__all__ = ["BrowserEngine", "PlaywrightEngine", "ChromiumCdpEngine", "get_browser_engine"]


def get_browser_engine() -> BrowserEngine:
    backend = (os.environ.get("BROWSER_BACKEND") or "playwright").strip().lower()
    if backend == "chromium":
        return ChromiumCdpEngine()
    return PlaywrightEngine()
