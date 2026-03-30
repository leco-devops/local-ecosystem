"""Abstract browser automation backend (Playwright vs system Chromium + CDP)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BrowserEngine(ABC):
    name: str

    @abstractmethod
    async def screenshot(self, url: str, *, timeout_ms: int, full_page: bool = False) -> bytes:
        ...

    @abstractmethod
    async def pdf(self, url: str, *, timeout_ms: int) -> bytes:
        ...

    @abstractmethod
    async def html(self, url: str, *, timeout_ms: int) -> str:
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release process / browser resources."""
        ...
